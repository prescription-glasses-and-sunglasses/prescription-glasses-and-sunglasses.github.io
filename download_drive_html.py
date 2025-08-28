import os
import json
import sys
import io
import random
import time
import re # 导入正则表达式模块
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ------------------------
# 服务账号配置
# ------------------------
service_account_info = os.environ.get("GDRIVE_SERVICE_ACCOUNT")
if not service_account_info:
    print("❌ 未找到 GDRIVE_SERVICE_ACCOUNT 环境变量。")
    sys.exit(1)

try:
    service_account_info = json.loads(service_account_info)
except json.JSONDecodeError:
    print("❌ 解析 GDRIVE_SERVICE_ACCOUNT 失败。请确保它是一个有效的 JSON 字符串。")
    sys.exit(1)

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
creds = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
service = build('drive', 'v3', credentials=creds)

# ------------------------
# 支持多文件夹 ID
# ------------------------
folder_ids_str = os.environ.get("GDRIVE_FOLDER_ID")
if not folder_ids_str:
    print("❌ 未找到 GDRIVE_FOLDER_ID 环境变量。")
    sys.exit(1)

FOLDER_IDS = [fid.strip() for fid in folder_ids_str.split(",") if fid.strip()]

# ------------------------
# 从 TXT 文件读取关键词
# ------------------------
keywords = []
keywords_file = "keywords.txt"
if os.path.exists(keywords_file):
    with open(keywords_file, "r", encoding="utf-8") as f:
        keywords = [line.strip() for line in f if line.strip()]

if not keywords:
    print("⚠️ keywords.txt 中没有找到关键词，将使用原始文件名。")

# ------------------------
# 记录已处理的文件 ID 和文件列表缓存
# ------------------------
processed_file_path = "processed_files.json"
cache_file_path = "files_cache.json"
CACHE_EXPIRY_HOURS = 24  # 缓存有效期（小时）

try:
    if os.path.exists(processed_file_path):
        with open(processed_file_path, "r") as f:
            processed_data = json.load(f)
    else:
        processed_data = {"fileIds": []}
except (json.JSONDecodeError, IOError) as e:
    print(f"读取 {processed_file_path} 时出错: {e}。将从一个空的已处理文件列表开始。")
    processed_data = {"fileIds": []}

def get_cached_files():
    """从缓存中读取文件列表，如果缓存过期则返回None。"""
    if os.path.exists(cache_file_path):
        try:
            with open(cache_file_path, "r") as f:
                cache_data = json.load(f)
                last_updated = cache_data.get("last_updated")
                if last_updated and (time.time() - last_updated < CACHE_EXPIRY_HOURS * 3600):
                    print("✅ 缓存未过期，正在从本地加载文件列表。")
                    return cache_data.get("files", [])
                else:
                    print(f"⏳ 缓存已过期（上次更新超过 {CACHE_EXPIRY_HOURS} 小时），将重新拉取文件列表。")
        except (json.JSONDecodeError, IOError) as e:
            print(f"读取 {cache_file_path} 时出错: {e}。将重新拉取文件列表。")
    return None

def save_files_to_cache(files):
    """将文件列表和当前时间戳保存到缓存文件。"""
    cache_data = {
        "last_updated": time.time(),
        "files": files
    }
    with open(cache_file_path, "w") as f:
        json.dump(cache_data, f, indent=4)
    print("💾 已将文件列表保存到本地缓存。")

# ------------------------
# 获取文件列表的函数 (已优化)
# ------------------------
def list_files(folder_id):
    """列出指定 Google Drive 文件夹中的所有文件，支持分页。"""
    all_the_files = []
    page_token = None
    query = f"'{folder_id}' in parents and (" \
            "mimeType='text/html' or " \
            "mimeType='text/plain' or " \
            "mimeType='application/vnd.google-apps.document')"
    try:
        while True:
            results = service.files().list(
                q=query,
                pageSize=1000,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token
            ).execute()
            items = results.get('files', [])
            all_the_files.extend(items)
            page_token = results.get('nextPageToken', None)
            if page_token is None:
                break
        print(f"  - 在文件夹 {folder_id} 中总共找到 {len(all_the_files)} 个文件。")
        return all_the_files
    except Exception as e:
        print(f"列出文件时发生错误: {e}")
        return []

# ------------------------
# 下载和生成 HTML
# ------------------------
def download_html_file(file_id, file_name):
    """下载一个 HTML 文件。"""
    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(file_name, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    print(f"✅ 已下载 {file_name}")

def download_txt_file(file_id, file_name, original_name):
    """下载一个文本文件并将其转换为 HTML。"""
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    text_content = fh.getvalue().decode('utf-8')
    
    # 检查内容是否已经是HTML格式
    is_html = text_content.strip().lower().startswith('<!doctype html') or text_content.strip().lower().startswith('<html')
    
    if is_html:
        # 如果已经是HTML格式，直接保存
        html_content = text_content
    else:
        # 如果不是HTML格式，则包装成HTML
        html_content = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{original_name}</title></head><body><pre>{text_content}</pre></body></html>"
    
    with open(file_name, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"✅ TXT 已转换为 HTML: {file_name}")

def export_google_doc(file_id, file_name):
    """将 Google 文档导出为 HTML。"""
    request = service.files().export_media(fileId=file_id, mimeType='text/html')
    fh = io.FileIO(file_name, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    print(f"✅ Google 文档已导出为 HTML: {file_name}")

# ------------------------
# 主程序
# ------------------------
all_files = get_cached_files()

if all_files is None:
    all_files = []
    print("⏳ 正在从 Google Drive 拉取所有文件列表...")
    for folder_id in FOLDER_IDS:
        print(f"📂 正在获取文件夹: {folder_id}")
        files = list_files(folder_id)
        all_files.extend(files)
    save_files_to_cache(all_files)

new_files = [f for f in all_files if f['id'] not in processed_data["fileIds"]]

if not new_files:
    print("✅ 没有新的文件需要处理。")
    # 即使没有新文件，也需要重新生成内部链接，以防万一
    print("重新生成所有页面的内部链接...")
else:
    print(f"发现 {len(new_files)} 个未处理文件。")
    num_to_process = min(len(new_files), 30)
    selected_files = random.sample(new_files, num_to_process)
    print(f"本次运行将处理 {len(selected_files)} 个文件。")

    available_keywords = list(keywords)
    keywords_ran_out = False

    for f in selected_files:
        if available_keywords:
            keyword = available_keywords.pop(0)
            safe_name = keyword + ".html"
        else:
            if not keywords_ran_out:
                print("⚠️ 关键词已用完，将使用原始文件名加随机后缀。")
                keywords_ran_out = True
            
            base_name = os.path.splitext(f['name'])[0]
            sanitized_name = base_name.replace(" ", "-").replace("/", "-")
            random_suffix = str(random.randint(1000, 9999))
            safe_name = f"{sanitized_name}-{random_suffix}.html"

        print(f"正在处理 '{f['name']}' -> '{safe_name}'")

        if f['mimeType'] == 'text/html':
            download_html_file(f['id'], safe_name)
        elif f['mimeType'] == 'text/plain':
            download_txt_file(f['id'], safe_name, f['name'])
        else: # 'application/vnd.google-apps.document'
            export_google_doc(f['id'], safe_name)

        processed_data["fileIds"].append(f['id'])

    with open(processed_file_path, "w") as f:
        json.dump(processed_data, f, indent=4)
    print(f"💾 已将 {len(selected_files)} 个新文件 ID 保存到 {processed_file_path}")

    with open(keywords_file, "w", encoding="utf-8") as f:
        for keyword in available_keywords:
            f.write(keyword + "\n")
    print(f"✅ 已用剩余的关键词更新 {keywords_file}")

# ------------------------
# 生成累积的站点地图
# ------------------------
existing_html_files = [f for f in os.listdir(".") if f.endswith(".html") and f != "index.html"]
index_content = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Reading Glasses</title></head><body>\n"
index_content += "<h1>Reading Glasses</h1>\n<ul>\n"
for fname in sorted(existing_html_files):
    index_content += f'<li><a href="{fname}">{fname}</a></li>\n'
index_content += "</ul>\n</body></html>"

with open("index.html", "w", encoding="utf-8") as f:
    f.write(index_content)
print("✅ 已生成 index.html (完整站点地图)")

# ------------------------
# 在每个页面底部添加随机内部链接 (已优化，不会累积)
# ------------------------
all_html_files = [f for f in os.listdir(".") if f.endswith(".html") and f != "index.html"]

for fname in all_html_files:
    try:
        with open(fname, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # 使用正则表达式移除所有已有的 footer 链接部分
        # re.DOTALL 允许 '.' 匹配换行符，re.IGNORECASE 忽略大小写
        # 正则表达式匹配从 <footer> 到 </footer> 之间的所有内容（非贪婪匹配）
        content = re.sub(r"<footer>.*?</footer>", "", content, flags=re.DOTALL | re.IGNORECASE)
        
        # 清理可能存在的多余的HTML结构（处理嵌套的HTML问题）
        content = re.sub(r"</body>\s*</html>\s*(?=<footer>|</body>)", "", content, flags=re.IGNORECASE)
        
        # 从潜在链接列表中排除当前文件
        other_files = [x for x in all_html_files if x != fname]
        # 确定要添加的随机链接数量（4 到 6 个之间）
        num_links = min(len(other_files), random.randint(4, 6))

        if num_links > 0:
            random_links = random.sample(other_files, num_links)
            links_html = "<footer><ul>\n" + "\n".join([f'<li><a href="{x}">{x}</a></li>' for x in random_links]) + "\n</ul></footer>"
            
            # 确保只保留最后一个</body></html>标签
            content = re.sub(r"</body>\s*</html>.*$", "", content, flags=re.IGNORECASE)
            content = content.strip() + "\n" + links_html + "</body></html>"

        with open(fname, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"无法为 {fname} 处理内部链接: {e}")

print("✅ 已为所有页面更新底部随机内部链接 (每个 4-6 个，完全刷新)")