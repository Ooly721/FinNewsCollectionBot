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

# RSS源地址列表（优化后的源，去掉了失效的）
rss_feeds = {
    "📈 财经要闻": {
        "华尔街见闻": "https://dedicated.wallstreetcn.com/rss.xml",
        "东方财富": "http://rss.eastmoney.com/rss_partener.xml",
    },
    "🌍 全球经济": {
        "BBC经济": "http://feeds.bbci.co.uk/news/business/rss.xml",
        "华尔街日报-经济": "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed",
    },
    "📊 市场动态": {
        "华尔街日报-市场": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        "MarketWatch": "https://www.marketwatch.com/rss/topstories",
        "ZeroHedge": "https://feeds.feedburner.com/zerohedge/feed",
    },
    "🤖 AI前沿": {
        "量子位": "https://www.qbitai.com/feed",
        "TechCrunch": "https://techcrunch.com/feed/",
        "Wired": "https://www.wired.com/feed/rss",
    },
    "💡 科技商业": {
        "钛媒体": "https://www.tmtpost.com/rss.xml",
    }
}

# 获取北京时间
def today_date():
    return datetime.now(pytz.timezone("Asia/Shanghai")).date()

def fetch_article_text(url, max_retries=2):
    """抓取文章正文，带重试机制"""
    for attempt in range(max_retries):
        try:
            print(f"📰 正在爬取文章内容: {url}")
            article = Article(url)
            article.download()
            article.parse()
            text = article.text[:3000]  # 增加到3000字符
            if not text:
                print(f"⚠️ 文章内容为空: {url}")
                return "（文章内容为空）"
            return text
        except Exception as e:
            print(f"⚠️ 第 {attempt+1} 次爬取失败: {url}，错误: {e}")
            if attempt < max_retries - 1:
                time.sleep(3)
            else:
                print(f"❌ 文章爬取最终失败: {url}")
                return "（未能获取文章正文）"

def fetch_feed_with_headers(url):
    """获取RSS源"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        return feedparser.parse(url, request_headers=headers)
    except Exception as e:
        print(f"❌ RSS请求失败: {e}")
        return None

def fetch_feed_with_retry(url, retries=3, delay=5):
    """带重试的RSS获取"""
    for i in range(retries):
        try:
            feed = fetch_feed_with_headers(url)
            if feed and hasattr(feed, 'entries') and len(feed.entries) > 0:
                return feed
            print(f"⚠️ 第 {i+1} 次获取 {url} 返回空数据")
        except Exception as e:
            print(f"⚠️ 第 {i+1} 次请求 {url} 失败: {e}")
        
        if i < retries - 1:
            time.sleep(delay)
    
    print(f"❌ 跳过 {url}, 尝试 {retries} 次后仍失败。")
    return None

def fetch_rss_articles(rss_feeds, max_articles=5):
    """获取所有RSS文章"""
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

                # 获取文章正文（用于AI分析）
                article_text = fetch_article_text(link)
                analysis_text += f"【{title}】\n{article_text}\n\n"
                print(f"🔹 {source} - {title[:50]}... 获取成功")
                
                articles.append(f"- [{title}]({link})")
                count += 1
                
                # 避免请求过快
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
    # 检查内容长度，避免超限
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

def main():
    """主函数"""
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
        
        # 4. 发送到微信
        print("\n📤 第四步：推送到微信...")
        title = f"📌 {today_str} 财经新闻摘要"
        send_to_wechat(title, final_summary)
        
        # 5. 保存本地备份（可选）
        save_local_backup(title, final_summary)
        
        print("\n✅ 任务执行完毕！")
        
    except Exception as e:
        print(f"❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()
        raise

if __name__ == "__main__":
    main()
