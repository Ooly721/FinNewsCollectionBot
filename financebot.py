# 福生无量天尊
from openai import OpenAI
import feedparser
import requests
from newspaper import Article
from datetime import datetime
import time
import pytz
import os
import json
import sqlite3
import hashlib
import re
from urllib.parse import urlparse

# ==================== 配置 ====================

# OpenAI API Key
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise ValueError("环境变量 OPENAI_API_KEY 未设置，请在Github Actions中设置此变量！")

# Server酱 SendKeys
server_chan_keys_env = os.getenv("SERVER_CHAN_KEYS")
if not server_chan_keys_env:
    raise ValueError("环境变量 SERVER_CHAN_KEYS 未设置，请在Github Actions中设置此变量！")
server_chan_keys = server_chan_keys_env.split(",")

openai_client = OpenAI(api_key=openai_api_key, base_url="https://api.deepseek.com/v1")

# RSS源地址列表
rss_feeds = {
    "💾 存储芯片 & 半导体": {
        "半导体行业观察": "https://www.semiinsights.com/feed",
        "Digitimes Asia": "https://www.digitimes.com/rss/asia.xml",
        "EE Times": "https://eetimes.com/feed",
    },
    "🔌 PCB & CPO & 交换机": {
        "PCB行业网": "https://www.pcbcn.org/rss.xml",
        "讯石光通讯网": "https://www.iccsz.com/rss.xml",
        "LightCounting": "https://www.lightcounting.com/rss.xml",
    },
    "⚛️ MLCC & 被动元件": {
        "村田制作所新闻": "https://www.murata.com/news/rss.xml",
        "太阳诱电公告": "https://www.yuden.co.jp/news/rss.xml",
    },
    "🚀 马斯克产业链": {
        "Teslarati": "https://www.teslarati.com/feed",
        "Electrek": "https://electrek.co/feed",
        "SpaceX中文资讯": "https://www.spacexchina.com/feed",
        "盖世汽车": "https://auto.gasgoo.com/news/rss.xml",
    },
    "🛢️ 油价 & 能源": {
        "国际能源署(IEA)": "https://www.iea.org/rss.xml",
        "OilPrice.com": "https://oilprice.com/rss.xml",
        "金吾财讯-能源": "https://www.jwview.com/rss/energy.xml",
    },
    "🌍 地缘政治 & 宏观": {
        "环球时报国际": "https://world.huanqiu.com/rss.xml",
        "参考消息": "https://www.cankaoxiaoxi.com/rss.xml",
        "日本财务省统计": "https://www.mof.go.jp/rss/rss.xml",
    },
    "📊 综合财经": {
        "华尔街见闻": "https://dedicated.wallstreetcn.com/rss.xml",
        "东方财富": "http://rss.eastmoney.com/rss_partener.xml",
        "TechCrunch": "https://techcrunch.com/feed/",
    }
}

DB_PATH = "news_history.db"
DEAD_SOURCES_FILE = "dead_sources.json"

# ==================== 工具函数 ====================

def today_date():
    return datetime.now(pytz.timezone("Asia/Shanghai")).date()

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """初始化数据库表（首次运行时自动创建）"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_date TEXT UNIQUE NOT NULL,
            ai_summary TEXT,
            full_content TEXT,
            raw_news_text TEXT,
            article_count INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_date TEXT NOT NULL,
            source_category TEXT,
            source_name TEXT,
            title TEXT,
            url TEXT,
            summary_preview TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_date) REFERENCES daily_news(news_date)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

def save_news_to_db(news_date, ai_summary, full_content, raw_news_text, articles_data):
    """保存新闻数据到数据库"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_count = 0
    article_list = []
    for category, content in articles_data.items():
        lines = content.strip().split('\n')
        for line in lines:
            if line.startswith('- ['):
                try:
                    title_part = line[3:line.find('](')]
                    url_part = line[line.find('](')+2:line.find(')')]
                    article_list.append({
                        'category': category,
                        'source': '',
                        'title': title_part,
                        'url': url_part
                    })
                    total_count += 1
                except:
                    continue
    
    try:
        cursor.execute('''
            INSERT INTO daily_news (news_date, ai_summary, full_content, raw_news_text, article_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(news_date) DO UPDATE SET
                ai_summary = excluded.ai_summary,
                full_content = excluded.full_content,
                raw_news_text = excluded.raw_news_text,
                article_count = excluded.article_count
        ''', (news_date, ai_summary, full_content, raw_news_text, total_count))
        
        cursor.execute('DELETE FROM news_articles WHERE news_date = ?', (news_date,))
        
        for article in article_list:
            cursor.execute('''
                INSERT INTO news_articles (news_date, source_category, source_name, title, url)
                VALUES (?, ?, ?, ?, ?)
            ''', (news_date, article['category'], article['source'], article['title'], article['url']))
        
        conn.commit()
        print(f"✅ 数据已保存到数据库，共 {total_count} 篇文章")
    except Exception as e:
        print(f"❌ 数据库保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def query_latest_news(limit=7):
    """查询最近N天的新闻摘要"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT news_date, ai_summary, article_count, created_at
        FROM daily_news
        ORDER BY news_date DESC
        LIMIT ?
    ''', (limit,))
    results = cursor.fetchall()
    conn.close()
    return results

def query_articles_by_keyword(keyword):
    """按关键词搜索历史文章"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT news_date, source_category, title, url
        FROM news_articles
        WHERE title LIKE ? OR source_category LIKE ?
        ORDER BY news_date DESC
    ''', (f'%{keyword}%', f'%{keyword}%'))
    results = cursor.fetchall()
    conn.close()
    return results

def get_statistics():
    """获取统计信息"""
    conn = get_db_connection()
    cursor = conn.cursor()
    stats = {}
    cursor.execute('SELECT COUNT(*) FROM daily_news')
    stats['total_days'] = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM news_articles')
    stats['total_articles'] = cursor.fetchone()[0]
    cursor.execute('''
        SELECT source_category, COUNT(*) as count
        FROM news_articles
        GROUP BY source_category
        ORDER BY count DESC
    ''')
    stats['category_distribution'] = cursor.fetchall()
    conn.close()
    return stats

# ==================== 死源管理 ====================

def load_dead_sources():
    """加载死源列表"""
    if os.path.exists(DEAD_SOURCES_FILE):
        try:
            with open(DEAD_SOURCES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_dead_sources(dead_sources):
    """保存死源列表"""
    try:
        with open(DEAD_SOURCES_FILE, 'w', encoding='utf-8') as f:
            json.dump(dead_sources, f, ensure_ascii=False, indent=2)
    except:
        pass

# ==================== 核心爬虫函数（修复版） ====================

def fetch_article_with_newspaper(url, timeout=15):
    """
    使用 newspaper3k 爬取文章（兼容所有版本）
    不传 timeout 参数，而是用 requests 先获取内容
    """
    try:
        # 先用 requests 获取页面（支持超时）
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return None
        
        # 再用 newspaper 解析 HTML
        article = Article(url)
        article.set_html(response.text)
        article.parse()
        text = article.text[:3000]
        if text and len(text) > 50:
            return text
        return None
    except Exception as e:
        print(f"⚠️ 爬取失败: {e}")
        return None

def fetch_eetimes_article(url):
    """专门爬取 EE Times 的文章"""
    try:
        print(f"📰 正在爬取 EE Times 文章: {url}")
        # 使用更长的超时
        return fetch_article_with_newspaper(url, timeout=25)
    except Exception as e:
        print(f"⚠️ EE Times 爬取失败: {e}")
        return None

def fetch_eastmoney_article(url):
    """专门爬取东方财富的文章（通过真实API）"""
    try:
        # 从URL提取文章ID
        match = re.search(r'/(\d+)\.html', url)
        if not match:
            return fetch_eastmoney_by_regex(url)
        
        article_id = match.group(1)
        
        # 东方财富的真实API
        api_url = f"https://finance.eastmoney.com/api/content/{article_id}"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://finance.eastmoney.com/',
            'Accept': 'application/json'
        }
        
        response = requests.get(api_url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            content = data.get('data', {}).get('content', '')
            if content and len(content) > 50:
                # 清理HTML标签
                content = re.sub(r'<[^>]+>', '', content)
                content = re.sub(r'&nbsp;', ' ', content)
                content = re.sub(r'\s+', ' ', content).strip()
                return content[:3000]
        
        return fetch_eastmoney_by_regex(url)
        
    except Exception as e:
        print(f"⚠️ 东方财富 API 抓取失败: {e}")
        return fetch_eastmoney_by_regex(url)

def fetch_eastmoney_by_regex(url):
    """备用方案：用正则从页面提取东方财富文章内容"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            # 尝试匹配文章内容的多种可能容器
            patterns = [
                r'<div class="content"[^>]*>(.*?)</div>',
                r'<div class="article-content"[^>]*>(.*?)</div>',
                r'<div id="ContentBody"[^>]*>(.*?)</div>',
                r'<div class="detail-content"[^>]*>(.*?)</div>',
                r'<div class="news-content"[^>]*>(.*?)</div>'
            ]
            for pattern in patterns:
                match = re.search(pattern, response.text, re.DOTALL)
                if match:
                    content = match.group(1)
                    # 清理HTML标签
                    content = re.sub(r'<[^>]+>', '', content)
                    content = re.sub(r'&nbsp;', ' ', content)
                    content = re.sub(r'&[a-z]+;', '', content)
                    content = re.sub(r'\s+', ' ', content).strip()
                    if len(content) > 100:
                        return content[:3000]
        return None
    except Exception as e:
        print(f"⚠️ 东方财富 正则提取失败: {e}")
        return None

def fetch_wallstreetcn_article(url):
    """专门爬取华尔街见闻的文章"""
    try:
        return fetch_article_with_newspaper(url, timeout=15)
    except Exception as e:
        print(f"⚠️ 华尔街见闻爬取失败: {e}")
        return None

def fetch_feed_with_headers(url):
    """获取RSS源"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache'
    }
    try:
        return feedparser.parse(url, request_headers=headers)
    except Exception as e:
        print(f"❌ RSS请求失败: {e}")
        return None

# ==================== 增强版核心函数 ====================

def fetch_article_text(url, max_retries=2, rss_summary=None):
    """
    增强版文章抓取，根据网站域名使用不同策略
    """
    # 判断URL所属网站
    domain = urlparse(url).netloc.lower()
    
    content = None
    
    # 1. EE Times - 使用长超时
    if 'eetimes.com' in domain:
        for attempt in range(max_retries):
            content = fetch_eetimes_article(url)
            if content:
                break
            print(f"⏳ EE Times 第 {attempt+1} 次重试...")
            time.sleep(3)
        if not content:
            print(f"❌ EE Times 文章爬取最终失败: {url}")
            return rss_summary or "（未能获取文章正文）"
        return content
    
    # 2. 东方财富 - 使用API
    if 'eastmoney' in domain or 'finance.eastmoney' in domain:
        for attempt in range(max_retries):
            content = fetch_eastmoney_article(url)
            if content:
                break
            print(f"⏳ 东方财富 第 {attempt+1} 次重试...")
            time.sleep(2)
        if not content:
            print(f"❌ 东方财富 文章爬取最终失败: {url}")
            return rss_summary or "（未能获取文章正文）"
        return content
    
    # 3. 华尔街见闻 - 专用处理
    if 'wallstreetcn' in domain:
        content = fetch_wallstreetcn_article(url)
        if not content:
            print(f"❌ 华尔街见闻 文章爬取最终失败: {url}")
            return rss_summary or "（未能获取文章正文）"
        return content
    
    # 4. 其他网站 - 通用方法（使用修复后的函数）
    for attempt in range(max_retries):
        timeout = 15 if attempt == 0 else 25
        content = fetch_article_with_newspaper(url, timeout=timeout)
        if content:
            return content
        print(f"⏳ 通用爬取 第 {attempt+1} 次重试...")
        time.sleep(2)
    
    print(f"❌ 文章爬取最终失败: {url}")
    return rss_summary or "（未能获取文章正文）"

def fetch_feed_with_retry(url, retries=3, delay=5):
    """增强版RSS获取，带死源自动过滤"""
    
    # 检查是否在死源列表中
    dead_sources = load_dead_sources()
    if url in dead_sources and dead_sources[url] >= 5:
        print(f"⏭️ 源 {url} 已被标记为失效（连续失败{dead_sources[url]}次），跳过")
        return None
    
    for i in range(retries):
        try:
            feed = fetch_feed_with_headers(url)
            if feed and hasattr(feed, 'entries') and len(feed.entries) > 0:
                # 如果之前是死源，现在恢复了，重置计数
                if url in dead_sources and dead_sources[url] >= 5:
                    dead_sources[url] = 0
                    save_dead_sources(dead_sources)
                    print(f"✅ 源 {url} 已恢复，重新激活")
                return feed
            print(f"⚠️ 第 {i+1} 次获取 {url} 返回空数据")
        except Exception as e:
            print(f"⚠️ 第 {i+1} 次请求 {url} 失败: {e}")
        
        if i < retries - 1:
            time.sleep(delay)
    
    # 连续失败，记录到死源
    dead_sources[url] = dead_sources.get(url, 0) + 1
    save_dead_sources(dead_sources)
    print(f"❌ 跳过 {url}, 尝试 {retries} 次后仍失败。（累计失败 {dead_sources[url]} 次）")
    return None

def fetch_rss_articles(rss_feeds, max_articles=5):
    """增强版获取所有RSS文章（使用RSS摘要作为降级方案）"""
    news_data = {}
    analysis_text = ""

    for category, sources in rss_feeds.items():
        category_content = ""
        for source, url in sources.items():
            print(f"📡 正在获取 {source} 的 RSS 源: {url}")
            feed = fetch_feed_with_retry(url)
            if not feed:
                print(f"⚠️ 无法获取 {source} 的 RSS 数据")
                continue
            
            print(f"✅ {source} RSS 获取成功，共 {len(feed.entries)} 条新闻")

            articles = []
            count = 0
            for entry in feed.entries:
                if count >= max_articles:
                    break
                title = entry.get('title', '无标题')
                link = entry.get('link', '') or entry.get('guid', '')
                if not link:
                    print(f"⚠️ {source} 的新闻 '{title}' 没有链接，跳过")
                    continue
                
                # 获取RSS中的摘要（作为降级方案）
                rss_summary = entry.get('summary', '') or entry.get('description', '') or ''
                # 清理HTML标签
                rss_summary = re.sub(r'<[^>]+>', '', rss_summary)
                rss_summary = re.sub(r'&[a-z]+;', '', rss_summary)
                rss_summary = rss_summary[:500].strip()
                
                # 爬取文章正文（传入RSS摘要作为降级）
                print(f"📰 正在爬取文章内容: {link}")
                article_text = fetch_article_text(link, max_retries=2, rss_summary=rss_summary)
                
                # 如果文章内容太短，用RSS摘要补充
                if (not article_text or len(article_text) < 50) and rss_summary:
                    article_text = f"（来自RSS摘要）{rss_summary}"
                    print(f"📝 使用RSS摘要替代: {link}")
                
                analysis_text += f"【{title}】\n{article_text}\n\n"
                print(f"🔹 {source} - {title[:50]}... 获取成功")
                articles.append(f"- [{title}]({link})")
                count += 1
                time.sleep(0.5)

            if articles:
                category_content += f"### {source}\n" + "\n".join(articles) + "\n\n"

        if category_content:
            news_data[category] = category_content

    return news_data, analysis_text

def summarize_news(text):
    """使用AI分析新闻"""
    if not text or len(text.strip()) < 100:
        return "（新闻内容不足，无法进行分析）"
    
    try:
        completion = openai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": """
你是一名专业的财经新闻分析师，请根据以下新闻内容，按照以下步骤完成任务：
             1. 提取新闻中涉及的主要行业和主题，找出近1天涨幅最高的3个行业或主题，以及近3天涨幅较高且此前2周表现平淡的3个行业/主题。
             2. 针对每个热点，输出：催化剂、复盘、展望。
             3. 将以上分析整合为一篇1500字以内的财经热点摘要。
                 """},
                {"role": "user", "content": text}
            ]
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ AI分析失败: {e}")
        return "（AI分析暂时不可用，请稍后重试）"

def send_to_wechat(title, content):
    """发送到微信（通过Server酱）"""
    if len(content) > 60000:
        content = content[:60000] + "\n\n...（内容过长，已截断）"
    
    success_count = 0
    for key in server_chan_keys:
        try:
            url = f"https://sctapi.ftqq.com/{key}.send"
            data = {"title": title, "desp": content}
            response = requests.post(url, data=data, timeout=30)
            if response.ok:
                print(f"✅ 推送成功 (密钥: {key[:8]}...)")
                success_count += 1
            else:
                print(f"❌ 推送失败 (密钥: {key[:8]}...)，状态码: {response.status_code}")
        except Exception as e:
            print(f"❌ 推送异常 (密钥: {key[:8]}...): {e}")
    
    if success_count == 0:
        print("⚠️ 所有密钥推送均失败，请检查网络和Server酱服务状态")

def save_local_backup(title, content):
    """保存本地备份（用于调试）"""
    try:
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"{title}\n\n{content}")
        print(f"✅ 本地备份已保存：{filename}")
    except Exception as e:
        print(f"⚠️ 保存本地备份失败: {e}")

def show_history():
    """显示最近7天的历史记录"""
    results = query_latest_news(7)
    print("\n📚 === 最近7天新闻记录 ===")
    if not results:
        print("（暂无历史数据）")
        return
    for row in results:
        summary_preview = row['ai_summary'][:50] if row['ai_summary'] else "（无摘要）"
        print(f"📅 {row['news_date']} | 文章数: {row['article_count']} | {summary_preview}...")
    
    stats = get_statistics()
    print(f"\n📊 总统计：共 {stats['total_days']} 天，{stats['total_articles']} 篇文章")
    print("📂 分类分布：")
    for cat in stats['category_distribution']:
        print(f"   {cat['source_category']}: {cat['count']} 篇")

# ==================== 主函数 ====================

def main():
    """主函数 - 执行采集 → AI分析 → 存数据库 → 推送微信"""
    
    # 初始化数据库
    init_database()
    
    try:
        today_str = today_date().strftime("%Y-%m-%d")
        print(f"🚀 开始执行财经新闻摘要任务 - {today_str}")
        
        # 1. 获取RSS文章
        print("\n📥 第一步：获取RSS文章...")
        articles_data, analysis_text = fetch_rss_articles(rss_feeds, max_articles=5)
        
        if not analysis_text:
            print("⚠️ 未能获取任何新闻内容，任务终止")
            return
        
        print(f"\n📊 共获取到 {len(analysis_text)} 字符的新闻内容")
        
        # 2. AI分析
        print("\n🤖 第二步：AI分析中...")
        summary = summarize_news(analysis_text)
        
        # 3. 组装最终内容
        print("\n📝 第三步：组装最终内容...")
        final_summary = f"📅 **{today_str} 财经新闻摘要**\n\n"
        final_summary += f"✍️ **今日分析总结：**\n{summary}\n\n"
        final_summary += "---\n\n"
        final_summary += "## 📰 新闻原文链接\n\n"
        
        for category, content in articles_data.items():
            if content.strip():
                final_summary += f"### {category}\n{content}\n\n"
        
        # 4. 保存到数据库
        print("\n💾 第四步：保存到数据库...")
        save_news_to_db(
            news_date=today_str,
            ai_summary=summary,
            full_content=final_summary,
            raw_news_text=analysis_text,
            articles_data=articles_data
        )
        
        # 5. 发送到微信
        print("\n📤 第五步：推送到微信...")
        title = f"📌 {today_str} 财经新闻摘要"
        send_to_wechat(title, final_summary)
        
        # 6. 保存本地备份
        save_local_backup(title, final_summary)
        
        print("\n✅ 任务执行完毕！")
        
    except Exception as e:
        print(f"❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()
        raise

# ==================== 入口 ====================

if __name__ == "__main__":
    import sys
    
    # 命令行参数处理
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "--history":
            show_history()
        elif command == "--search" and len(sys.argv) > 2:
            keyword = sys.argv[2]
            results = query_articles_by_keyword(keyword)
            print(f"\n🔍 搜索 '{keyword}' 结果：")
            if not results:
                print("（未找到相关文章）")
            for r in results:
                print(f"  📅 {r['news_date']} | {r['source_category']} | {r['title']}")
        elif command == "--stats":
            stats = get_statistics()
            print(f"\n📊 总统计：共 {stats['total_days']} 天，{stats['total_articles']} 篇文章")
            print("📂 分类分布：")
            for cat in stats['category_distribution']:
                print(f"   {cat['source_category']}: {cat['count']} 篇")
        else:
            print("可用命令: --history, --search <关键词>, --stats")
    else:
        main()