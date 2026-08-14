from flask import Flask, render_template, jsonify, request
import sqlite3
from datetime import datetime, timedelta
import json
import requests
import os
import base64

app = Flask(__name__)

# ==================== 配置 ====================
GITHUB_USERNAME = "Ooly721"  # ✅ 你的用户名
GITHUB_REPO = "FinNewsCollectionBot"
GITHUB_BRANCH = "main"
DB_PATH = "news_history.db"

def download_db_from_github():
    """从 GitHub 下载最新的 news_history.db"""
    url = f"https://raw.githubusercontent.com/{GITHUB_USERNAME}/{GITHUB_REPO}/{GITHUB_BRANCH}/news_history.db"
    
    print(f"🔗 正在访问: {url}")
    
    try:
        response = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        print(f"📊 HTTP 状态码: {response.status_code}")
        
        if response.status_code == 200:
            with open(DB_PATH, 'wb') as f:
                f.write(response.content)
            print(f"✅ 从 GitHub 下载数据库成功 (大小: {len(response.content)} 字节)")
            return True
        else:
            print(f"⚠️ GitHub 上暂无数据库文件 (HTTP {response.status_code})")
            return False
    except Exception as e:
        print(f"⚠️ 下载数据库失败: {e}")
        return False

def get_db_connection():
    """获取数据库连接 - 强制从 GitHub 拉取最新数据"""
    
    # 1. 强制从 GitHub 下载最新数据库（覆盖本地）
    print("📥 正在从 GitHub 拉取最新数据...")
    download_db_from_github()
    
    # 2. 如果本地没有数据库，再尝试下载一次
    if not os.path.exists(DB_PATH):
        print("⚠️ 本地没有数据库，再次尝试从 GitHub 下载...")
        download_db_from_github()
    
    # 3. 如果还是没有，创建空数据库
    if not os.path.exists(DB_PATH):
        print("⚠️ 数据库文件不存在，创建空数据库")
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    
    # 4. 连接数据库
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # 5. 打印数据统计
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as count FROM daily_news')
    result = cursor.fetchone()
    days = result['count'] if result else 0
    cursor.execute('SELECT COUNT(*) as count FROM news_articles')
    result = cursor.fetchone()
    articles = result['count'] if result else 0
    print(f"📊 数据库状态: {days} 天, {articles} 篇文章")
    
    return conn

# ==================== 页面路由 ====================

@app.route('/')
def index():
    """主页：Dashboard 看板"""
    return render_template('dashboard.html')

# ==================== API 接口 ====================

@app.route('/api/overview')
def api_overview():
    """总览统计：总天数、总文章数、今日文章数、各分类数量"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 总天数
    cursor.execute('SELECT COUNT(*) as count FROM daily_news')
    result = cursor.fetchone()
    total_days = result['count'] if result else 0
    
    # 总文章数
    cursor.execute('SELECT COUNT(*) as count FROM news_articles')
    result = cursor.fetchone()
    total_articles = result['count'] if result else 0
    
    # 今日文章数
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('SELECT COUNT(*) as count FROM news_articles WHERE news_date = ?', (today,))
    result = cursor.fetchone()
    today_count = result['count'] if result else 0
    
    # 各分类统计（全部历史）
    cursor.execute('''
        SELECT source_category, COUNT(*) as count
        FROM news_articles
        WHERE source_category IS NOT NULL AND source_category != ''
        GROUP BY source_category
        ORDER BY count DESC
    ''')
    category_stats = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    return jsonify({
        'total_days': total_days,
        'total_articles': total_articles,
        'today_count': today_count,
        'category_stats': category_stats
    })

@app.route('/api/trend')
def api_trend():
    """趋势数据：过去N天各分类每日文章数（用于折线图）"""
    days = request.args.get('days', 7, type=int)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    from datetime import timezone
    china_tz = timezone(timedelta(hours=8))
    today_china = datetime.now(china_tz).date()
    
    # 获取过去 N 天的日期列表
    date_list = []
    for i in range(days - 1, -1, -1):
        date_list.append((today_china - timedelta(days=i)).strftime('%Y-%m-%d'))
    
    fixed_categories = [
        "💾 存储芯片 & 半导体", 
        "🔌 PCB & CPO & 交换机", 
        "⚛️ MLCC & 被动元件", 
        "🚀 马斯克产业链", 
        "🛢️ 油价 & 能源", 
        "🌍 地缘政治 & 宏观", 
        "📊 综合财经"
    ]
    
    categories_data = {}
    for category in fixed_categories:
        categories_data[category] = {date: 0 for date in date_list}
    
    cursor.execute('''
        SELECT news_date, source_category, COUNT(*) as count
        FROM news_articles
        GROUP BY news_date, source_category
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    for row in rows:
        date = row['news_date']
        category = row['source_category']
        count = row['count']
        
        if date in date_list and category in categories_data:
            categories_data[category][date] = count
    
    series = []
    for category, data in categories_data.items():
        series.append({
            'name': category,
            'type': 'line',
            'data': [data[date] for date in date_list],
            'smooth': True
        })
    
    return jsonify({
        'dates': date_list,
        'series': series
    })

@app.route('/api/recent')
def api_recent():
    """最近文章列表"""
    limit = request.args.get('limit', 20, type=int)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT news_date, source_category, title, url
        FROM news_articles
        ORDER BY news_date DESC, id DESC
        LIMIT ?
    ''', (limit,))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)

@app.route('/api/search')
def api_search():
    """搜索关键词"""
    keyword = request.args.get('q', '').strip()
    if not keyword:
        return jsonify([])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT news_date, source_category, title, url
        FROM news_articles
        WHERE title LIKE ? OR source_category LIKE ?
        ORDER BY news_date DESC
        LIMIT 100
    ''', (f'%{keyword}%', f'%{keyword}%'))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)

@app.route('/api/daily_detail')
def api_daily_detail():
    """某天的详细文章列表"""
    date_str = request.args.get('date', '')
    if not date_str:
        return jsonify([])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT source_category, title, url
        FROM news_articles
        WHERE news_date = ?
        ORDER BY source_category
    ''', (date_str,))
    
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(results)

@app.route('/api/github_status')
def api_github_status():
    """检查 GitHub 数据库状态"""
    url = f"https://raw.githubusercontent.com/{GITHUB_USERNAME}/{GITHUB_REPO}/{GITHUB_BRANCH}/news_history.db"
    try:
        response = requests.head(url, timeout=10)
        if response.status_code == 200:
            last_modified = response.headers.get('Last-Modified', '未知')
            content_length = response.headers.get('Content-Length', '未知')
            return jsonify({
                'status': 'ok',
                'last_modified': last_modified,
                'size_bytes': content_length,
                'url': url
            })
        else:
            return jsonify({
                'status': 'not_found',
                'message': f'数据库文件不存在 (HTTP {response.status_code})'
            })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@app.route('/api/force_sync')
def api_force_sync():
    """手动强制从 GitHub 同步数据库"""
    success = download_db_from_github()
    return jsonify({
        'success': success,
        'message': '数据库同步成功' if success else '数据库同步失败，请检查网络或 GitHub 仓库'
    })

if __name__ == '__main__':
    # 启动时自动从 GitHub 拉取最新数据
    print("🚀 正在启动 Web 看板...")
    print(f"📂 GitHub 仓库: {GITHUB_USERNAME}/{GITHUB_REPO}")
    download_db_from_github()
    app.run(debug=True, host='0.0.0.0', port=5000)