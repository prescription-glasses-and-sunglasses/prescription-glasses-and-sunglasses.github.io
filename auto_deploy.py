# auto_deploy.py - 自动生成和部署卫星站点
import os
import sys
import random
import shutil
import json
import csv
import hashlib
from pathlib import Path
import subprocess
import requests
import time
import re
import string
import io
from dataclasses import dataclass
import dataclasses
from typing import List, Dict, Optional, Set
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# -----------------------------
# 日志配置
# -----------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
import requests
import time
import re
from dataclasses import dataclass
import dataclasses
from typing import List, Dict, Optional, Set
import logging

# -----------------------------
# 日志配置
# -----------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# -----------------------------
# 配置区
# -----------------------------
@dataclass
class PlatformConfig:
    netlify_token: str
    vercel_token: str
    github_token: str      # GitHub Personal Access Token
    github_username: str
    satellite_count: int
    repo_prefix: str       # GitHub仓库名称前缀

CONFIG_FILE = Path("config.csv")
GDRIVE_CONFIG_FILE = Path("gdrive_config.txt")  # Google Drive 配置文件
CROSS_PLATFORM_LINKS_FILE = "cross_platform_links.txt"  # 可选跨平台链轮 TXT
PROCESSED_FILES_PATH = Path("processed_files.json")  # 记录已处理的文件
CACHE_FILE_PATH = Path("files_cache.json")  # Google Drive 文件列表缓存

# 缓存配置
CACHE_DIR = Path(".cache")  # 缓存目录
SITES_CACHE_FILE = CACHE_DIR / "deployed_sites.json"  # 已部署站点缓存
CACHE_EXPIRY_HOURS = 24  # 缓存过期时间（小时）

# API请求配置
REQUEST_TIMEOUT = 30  # API请求超时时间
MAX_RETRIES = 3  # API请求最大重试次数

# 确保必要的目录都存在
CACHE_DIR.mkdir(exist_ok=True)

# -----------------------------
# Google Drive 文件处理
# -----------------------------
def get_google_drive_service():
    """初始化 Google Drive service"""
    try:
        from google.oauth2 import service_account
        
        # 首先尝试从环境变量获取服务账号信息
        service_account_info = os.environ.get('GDRIVE_SERVICE_ACCOUNT')
        if service_account_info:
            try:
                credentials_info = json.loads(service_account_info)
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info,
                    scopes=['https://www.googleapis.com/auth/drive.readonly']
                )
                logger.info("使用环境变量中的服务账号凭据")
                return build('drive', 'v3', credentials=credentials)
            except json.JSONDecodeError:
                logger.warning("环境变量 GDRIVE_SERVICE_ACCOUNT 格式不正确，尝试从文件读取")
        
        # 如果环境变量不存在或无效，尝试从文件读取
        if os.path.exists('service-account.json'):
            try:
                credentials = service_account.Credentials.from_service_account_file(
                    'service-account.json',
                    scopes=['https://www.googleapis.com/auth/drive.readonly']
                )
                logger.info("使用本地 service-account.json 文件的服务账号凭据")
                return build('drive', 'v3', credentials=credentials)
            except Exception as e:
                logger.error(f"读取 service-account.json 文件失败: {e}")
        
        logger.error("未找到有效的服务账号凭据，请确保设置了环境变量 GDRIVE_SERVICE_ACCOUNT 或提供了 service-account.json 文件")
        return None
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"初始化 Google Drive service 失败: {e}")
        return None

# 初始化 Google Drive service
service = get_google_drive_service()
def get_drive_files(folder_id: str) -> List[Dict]:
    """从 Google Drive 获取指定文件夹中的所有文件"""
    all_files = []
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
            all_files.extend(items)
            page_token = results.get('nextPageToken', None)
            if page_token is None:
                break
        logger.info(f"在文件夹 {folder_id} 中找到 {len(all_files)} 个文件")
        return all_files
    except Exception as e:
        logger.error(f"获取 Google Drive 文件列表时出错: {e}")
        return []

def download_drive_file(file_id: str, output_path: Path, mime_type: str) -> bool:
    """下载 Google Drive 文件并保存到指定路径"""
    try:
        if mime_type == 'text/html':
            request = service.files().get_media(fileId=file_id)
            with open(output_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        
        elif mime_type == 'text/plain':
            request = service.files().get_media(fileId=file_id)
            content = io.BytesIO()
            downloader = MediaIoBaseDownload(content, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text_content = content.getvalue().decode('utf-8')
            
            # 如果内容已经是HTML格式，直接保存
            if text_content.strip().lower().startswith(('<!doctype html', '<html')):
                html_content = text_content
            else:
                # 将纯文本转换为HTML
                html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>{output_path.stem}</title>
</head>
<body>
    <pre>{text_content}</pre>
</body>
</html>"""
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
        
        elif mime_type == 'application/vnd.google-apps.document':
            request = service.files().export_media(fileId=file_id, mimeType='text/html')
            with open(output_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        
        return True
    except Exception as e:
        logger.error(f"下载文件 {file_id} 时出错: {e}")
        return False

# -----------------------------
# Helper: 配置管理
# -----------------------------
def create_example_config():
    """创建示例配置文件并显示配置说明"""
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # 写入表头
            writer.writerow(['netlify_token', 'vercel_token', 'github_token', 'github_username', 'satellite_count', 'repo_prefix', 'gdrive_folder_ids'])
            # 写入示例数据
            writer.writerow([
                'your_netlify_token',          # Netlify API Token
                'your_vercel_token',           # Vercel API Token
                'your_github_token',           # GitHub Personal Access Token
                'your_github_username',        # GitHub用户名
                '10',                          # 要创建的卫星站数量
                'mysite',                      # 仓库名称前缀
                'folder_id1,folder_id2'        # Google Drive 文件夹ID，多个用逗号分隔
            ])
        
        # 打印配置说明
        logger.info(f"已创建配置文件: {CONFIG_FILE}")
        logger.info("\n配置说明：")
        logger.info("1. netlify_token: Netlify的API令牌")
        logger.info("2. vercel_token: Vercel的API令牌")
        logger.info("3. github_token: GitHub的个人访问令牌")
        logger.info("4. github_username: GitHub用户名")
        logger.info("5. satellite_count: 要创建的卫星站数量，直接在此设置即可")
        logger.info("6. repo_prefix: 仓库名称前缀，会自动添加随机字符，如：mysite_a7b2c9d4")
        logger.info("\n请编辑配置文件并填入正确的信息。")
        logger.info(f"已创建示例配置文件: {CONFIG_FILE}")
        logger.info("请编辑配置文件并填入正确的token和用户名")
        sys.exit(1)

def load_platform_config() -> PlatformConfig:
    """加载平台配置"""
    # 首先尝试从CSV文件加载
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                config = next(reader)
                return PlatformConfig(
                    netlify_token=config['netlify_token'],
                    vercel_token=config['vercel_token'],
                    github_token=config['github_token'],
                    github_username=config['github_username'],
                    satellite_count=int(config['satellite_count']),
                    repo_prefix=config['repo_prefix']
                )
        except Exception as e:
            logger.error(f"读取配置文件出错: {e}")
    
    # 回退到环境变量
    return PlatformConfig(
        netlify_token=os.getenv("NETLIFY_TOKEN", ""),
        vercel_token=os.getenv("VERCEL_TOKEN", ""),
        github_token=os.getenv("GITHUB_TOKEN", ""),
        github_username=os.getenv("GITHUB_USERNAME", "YOUR_USERNAME"),
        satellite_count=int(os.getenv("SATELLITE_COUNT", "10")),
        repo_prefix=os.getenv("REPO_PREFIX", "mysite")
    )

# -----------------------------
# Helper: 缓存管理
# -----------------------------
@dataclass
class FileState:
    """文件状态信息"""
    path: str
    modified_time: float
    content_hash: str

class CacheManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.files_cache = cache_dir / "files_state.json"
        self.sites_cache = cache_dir / "deployed_sites.json"
        self.file_states: Dict[str, FileState] = {}
        self.deployed_sites: Set[str] = set()
        self._load_caches()

    def _load_caches(self):
        """加载所有缓存"""
        # 加载文件状态缓存
        if self.files_cache.exists():
            try:
                with open(self.files_cache, "r") as f:
                    data = json.load(f)
                    if time.time() - data.get("last_updated", 0) < CACHE_EXPIRY_HOURS * 3600:
                        for path, state in data.get("files", {}).items():
                            self.file_states[path] = FileState(**state)
            except Exception as e:
                logger.error(f"加载文件状态缓存出错: {e}")

        # 加载已部署站点缓存
        if self.sites_cache.exists():
            try:
                with open(self.sites_cache, "r") as f:
                    data = json.load(f)
                    self.deployed_sites = set(data.get("sites", []))
            except Exception as e:
                logger.error(f"加载已部署站点缓存出错: {e}")

    def save_caches(self):
        """保存所有缓存"""
        # 保存文件状态缓存
        try:
            data = {
                "last_updated": time.time(),
                "files": {path: dataclasses.asdict(state) 
                         for path, state in self.file_states.items()}
            }
            with open(self.files_cache, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"保存文件状态缓存出错: {e}")

        # 保存已部署站点缓存
        try:
            data = {
                "sites": list(self.deployed_sites)
            }
            with open(self.sites_cache, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"保存已部署站点缓存出错: {e}")

    def get_file_state(self, file_path: Path) -> Optional[FileState]:
        """获取文件状态"""
        return self.file_states.get(str(file_path))

    def update_file_state(self, file_path: Path):
        """更新文件状态"""
        try:
            stat = file_path.stat()
            with open(file_path, "rb") as f:
                content_hash = hashlib.md5(f.read()).hexdigest()
            
            self.file_states[str(file_path)] = FileState(
                path=str(file_path),
                modified_time=stat.st_mtime,
                content_hash=content_hash
            )
        except Exception as e:
            logger.error(f"更新文件状态出错: {e}")

    def is_file_changed(self, file_path: Path) -> bool:
        """检查文件是否有变化"""
        old_state = self.get_file_state(file_path)
        if not old_state:
            return True

        try:
            stat = file_path.stat()
            if stat.st_mtime > old_state.modified_time:
                with open(file_path, "rb") as f:
                    current_hash = hashlib.md5(f.read()).hexdigest()
                return current_hash != old_state.content_hash
        except Exception as e:
            logger.error(f"检查文件变化出错: {e}")
            return True

        return False

    def is_site_deployed(self, site_url: str) -> bool:
        """检查站点是否已部署"""
        return site_url in self.deployed_sites

    def add_deployed_site(self, site_url: str):
        """添加已部署的站点"""
        self.deployed_sites.add(site_url)

# 创建缓存管理器实例
cache_manager = CacheManager(CACHE_DIR)

# 全局配置变量
GITHUB_TOKEN = None
GITHUB_USERNAME = None
NETLIFY_TOKEN = None
VERCEL_TOKEN = None
SATELLITE_COUNT = 10
REPO_PREFIX = "mysite"

# -----------------------------
# Helper: 从 Google Drive 获取文章
# -----------------------------
def get_drive_content(file_id: str, mime_type: str) -> Optional[str]:
    """从 Google Drive 获取文件内容"""
    try:
        if mime_type == 'text/html':
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue().decode('utf-8')
        
        elif mime_type == 'text/plain':
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text_content = fh.getvalue().decode('utf-8')
            
            # 如果内容已经是HTML格式，直接返回
            if text_content.strip().lower().startswith(('<!doctype html', '<html')):
                return text_content
            else:
                # 将纯文本转换为HTML
                return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>Generated Content</title>
</head>
<body>
    <pre>{text_content}</pre>
</body>
</html>"""
        
        elif mime_type == 'application/vnd.google-apps.document':
            request = service.files().export_media(fileId=file_id, mimeType='text/html')
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return fh.getvalue().decode('utf-8')
        
        return None
    except Exception as e:
        logger.error(f"获取文件 {file_id} 内容时出错: {e}")
        return None

def load_articles_from_drive() -> List[Dict[str, str]]:
    """从 Google Drive 获取文章内容"""
    # 从环境变量获取 Google Drive 文件夹 ID
    folder_ids_str = os.environ.get("GDRIVE_FOLDER_ID")
    if not folder_ids_str:
        logger.error("未找到 GDRIVE_FOLDER_ID 环境变量")
        return []
    
    folder_ids = [fid.strip() for fid in folder_ids_str.split(",") if fid.strip()]
    
    # 加载已处理的文件记录
    processed_files = set()
    if PROCESSED_FILES_PATH.exists():
        try:
            with open(PROCESSED_FILES_PATH, "r") as f:
                processed_data = json.load(f)
                processed_files = set(processed_data.get("fileIds", []))
        except Exception as e:
            logger.error(f"读取已处理文件记录时出错: {e}")
    
    # 获取所有文件
    all_files = []
    for folder_id in folder_ids:
        files = get_drive_files(folder_id)
        all_files.extend(files)
    
    # 过滤出未处理的文件
    new_files = [f for f in all_files if f['id'] not in processed_files]
    
    if not new_files:
        logger.info("没有新的文件需要处理")
        return []
    
    # 随机选择要处理的文件
    num_to_process = min(len(new_files), 30)
    selected_files = random.sample(new_files, num_to_process)
    logger.info(f"本次将处理 {len(selected_files)} 个新文件")
    
    # 获取文件内容
    articles = []
    for file in selected_files:
        content = get_drive_content(file['id'], file['mimeType'])
        if content:
            articles.append({
                'id': file['id'],
                'name': file['name'],
                'content': content
            })
            processed_files.add(file['id'])
    
    # 更新已处理文件记录
    with open(PROCESSED_FILES_PATH, "w") as f:
        json.dump({"fileIds": list(processed_files)}, f, indent=4)
    
    logger.info(f"成功获取 {len(articles)} 个文件的内容")
    return articles
    all_files = []
    for folder_id in folder_ids:
        files = get_drive_files(folder_id)
        all_files.extend(files)
    
    # 过滤出未处理的文件
    new_files = [f for f in all_files if f['id'] not in processed_files]
    
    # 如果没有新文件，返回空列表
    if not new_files:
        logger.info("没有新的文件需要处理")
        return []
    
    # 随机选择要处理的文件（避免一次处理太多）
    num_to_process = min(len(new_files), 30)
    selected_files = random.sample(new_files, num_to_process)
    logger.info(f"本次将处理 {len(selected_files)} 个新文件")
    
    # 获取文件内容
    articles = []
    for file in selected_files:
        content = get_drive_content(file['id'], file['mimeType'])
        if content:
            articles.append({
                'id': file['id'],
                'name': file['name'],
                'content': content
            })
            processed_files.add(file['id'])
    
    # 更新已处理文件记录
    with open(PROCESSED_FILES_PATH, "w") as f:
        json.dump({"fileIds": list(processed_files)}, f, indent=4)
    
    logger.info(f"成功下载 {len(downloaded_files)} 个文件")
    return downloaded_files

# -----------------------------
# Helper: 读取跨平台链接
# -----------------------------
def load_cross_links(file_path: str) -> List[str]:
    """读取跨平台链接"""
    if not Path(file_path).exists():
        logger.info(f"Cross-platform links file {file_path} not found")
        return []
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"Error reading cross-platform links: {e}")
        return []

# -----------------------------
# Helper: 生成链轮HTML
# -----------------------------
def generate_footer_html(links: List[str]) -> str:
    """生成footer HTML，支持移除旧的footer并添加新的链接"""
    footer_html = "\n<footer class='link-wheel'>\n<h3>相关链接</h3>\n<ul>\n"
    for link in links:
        if link.startswith('http'):
            # 外部链接添加nofollow和随机文本
            random_text = f"推荐阅读 {random.randint(1, 100)}"
            footer_html += f"<li><a href='{link}' rel='nofollow'>{random_text}</a></li>\n"
        else:
            # 本地文件链接使用文件名作为锚文本
            link_name = Path(link).stem
            desc = f"相关主题 {random.randint(1, 100)}"
            footer_html += f"<li><a href='./{link_name}.html'>{desc}</a></li>\n"
    footer_html += "</ul>\n</footer>\n"
    return footer_html

# -----------------------------
# Helper: 生成链轮
# -----------------------------
def generate_footer_links(platform_articles: List[Path], cross_links: List[str] = None, 
                         internal_count: int = 4, cross_count: int = 1) -> List[str]:
    """生成footer链接列表"""
    # 转换文章路径为相对路径字符串
    article_paths = [str(article.name) for article in platform_articles]
    
    internal_links = random.sample(article_paths, min(internal_count, len(article_paths)))
    cross_links_selected = []
    
    if cross_links:
        cross_links_selected = random.sample(cross_links, min(cross_count, len(cross_links)))
    
    footer_links = internal_links + cross_links_selected
    random.shuffle(footer_links)
    return footer_links

# -----------------------------
# Helper: 复制和修改文章
# -----------------------------
def prepare_satellite_content(source_articles: List[Path], target_dir: Path, 
                            footer_links: List[str]) -> bool:
    """准备卫星站内容"""
    try:
        # 确保目标目录存在
        target_dir.mkdir(parents=True, exist_ok=True)
        
        footer_html = generate_footer_html(footer_links)
        
        for article in source_articles:
            target_file = target_dir / article.name
            
            # 读取原文章内容
            with open(article, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 使用正则表达式移除旧的footer
            content = re.sub(r"<footer.*?</footer>", "", content, flags=re.DOTALL | re.IGNORECASE)
            
            # 清理可能存在的多余的HTML结构
            content = re.sub(r"</body>\s*</html>\s*(?=<footer>|</body>)", "", content, flags=re.IGNORECASE)
            
            # 确保内容是完整的HTML
            if not content.strip().lower().startswith('<!doctype html'):
                content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{article.stem}</title>
</head>
<body>
{content}
</body>
</html>"""
            
            # 在</body>标签前插入footer
            content = re.sub(r"</body>\s*</html>.*$", "", content, flags=re.IGNORECASE)
            content = content.strip() + "\n" + footer_html + "\n</body></html>"
            
            # 写入目标文件
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)
            
        # 创建基本的index.html
        index_content = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>卫星站点</title>
</head>
<body>
    <h1>文章列表</h1>
    <ul>
"""
        for article in source_articles:
            article_name = article.stem
            index_content += f'        <li><a href="{article.name}">{article_name}</a></li>\n'
        
        index_content += """    </ul>
</body>
</html>"""
        
        with open(target_dir / "index.html", "w", encoding="utf-8") as f:
            f.write(index_content)
            
        return True
        
    except Exception as e:
        logger.error(f"Error preparing satellite content: {e}")
        return False

# -----------------------------
# Helper: 生成随机仓库名
# -----------------------------
def generate_repo_name(prefix: str) -> str:
    """生成随机仓库名称
    格式: prefix_8位随机字母数字
    """
    # 生成8位随机字母数字组合
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{prefix}_{random_str}"

# -----------------------------
# Helper: Git操作
# -----------------------------
def create_github_repo(repo_name: str) -> Optional[str]:
    """创建GitHub仓库"""
    if not GITHUB_TOKEN:
        logger.error("GitHub token not found")
        return None

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    data = {
        "name": repo_name,
        "private": True,  # 设置为私有仓库
        "auto_init": False
    }
    
    url = "https://api.github.com/user/repos"
    result = make_api_request(url, headers, data)
    
    if result:
        logger.info(f"Created GitHub repository: {repo_name}")
        return result.get("clone_url")
    return None

def git_push(repo_path: Path, repo_url: str) -> bool:
    """Git推送操作"""
    cwd = os.getcwd()
    try:
        os.chdir(repo_path)
        
        # 设置Git凭证
        repo_url_with_token = repo_url.replace(
            "https://", f"https://{GITHUB_USERNAME}:{GITHUB_TOKEN}@"
        )
        
        # 检查是否已经是git仓库
        if not (repo_path / ".git").exists():
            subprocess.run(["git", "init"], check=True, capture_output=True)
            subprocess.run(["git", "branch", "-M", "main"], check=True, capture_output=True)
        
        subprocess.run(["git", "add", "."], check=True, capture_output=True)
        
        # 检查是否有变更
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode == 0:
            logger.info("No changes to commit")
            return True
            
        subprocess.run(["git", "commit", "-m", f"Update content {int(time.time())}"], 
                      check=True, capture_output=True)
        
        # 设置remote
        subprocess.run(["git", "remote", "remove", "origin"], 
                      capture_output=True, text=True, check=False)
        subprocess.run(["git", "remote", "add", "origin", repo_url_with_token], 
                      check=True, capture_output=True)
        
        subprocess.run(["git", "push", "-u", "origin", "main", "--force"], 
                      check=True, capture_output=True)
        logger.info(f"Successfully pushed to {repo_url}")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Git push failed: {e}")
        return False
    finally:
        os.chdir(cwd)

# -----------------------------
# Helper: 创建仓库内容
# -----------------------------
def create_repo_with_content(repo_name: str, articles: List[Dict[str, str]], cross_links: List[str]) -> Optional[str]:
    """创建仓库并添加内容"""
    # 创建GitHub仓库
    repo_url = create_github_repo(repo_name)
    if not repo_url:
        return None
    
    # 创建临时目录
    repo_path = Path(f".repos/{repo_name}")
    repo_path.mkdir(parents=True, exist_ok=True)
    
    try:
        # 生成站点内容
        files = create_site_content(articles, cross_links)
        
        # 创建所有文件
        for file_name, content in files.items():
            file_path = repo_path / file_name
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        
        # 推送到GitHub
        if git_push(repo_path, repo_url):
            return repo_url
        return None
        
    except Exception as e:
        logger.error(f"创建仓库内容失败: {e}")
        return None
    finally:
        # 清理临时文件
        if repo_path.exists():
            shutil.rmtree(repo_path)

# -----------------------------
# Helper: API请求重试
# -----------------------------
def make_api_request(url: str, headers: dict, data: dict = None, 
                    method: str = "POST") -> Optional[dict]:
    """带重试的API请求"""
    for attempt in range(MAX_RETRIES):
        try:
            if method.upper() == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=REQUEST_TIMEOUT)
            else:
                response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            
            if response.status_code in [200, 201]:
                return response.json()
            else:
                logger.warning(f"API request failed (attempt {attempt + 1}): {response.status_code} - {response.text}")
                
        except requests.RequestException as e:
            logger.warning(f"API request error (attempt {attempt + 1}): {e}")
        
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)  # 指数退避
    
    return None

# -----------------------------
# Helper: Netlify API 创建站点
# -----------------------------
def create_netlify_site(repo_url: str, site_name: Optional[str] = None) -> Optional[str]:
    """创建Netlify站点"""
    if not NETLIFY_TOKEN:
        logger.warning("NETLIFY_TOKEN not found in environment variables")
        return None
        
    headers = {"Authorization": f"Bearer {NETLIFY_TOKEN}", "Content-Type": "application/json"}
    data = {
        "name": site_name or f"sat-{int(time.time())}-{random.randint(1000, 9999)}",
        "build_settings": {
            "provider": "github",
            "repo": repo_url,
            "branch": "main"
        }
    }
    
    result = make_api_request("https://api.netlify.com/api/v1/sites", headers, data)
    if result:
        site_url = result.get("url") or result.get("ssl_url")
        logger.info(f"Netlify site created: {site_url}")
        return site_url
    else:
        logger.error("Failed to create Netlify site")
        return None

# -----------------------------
# Helper: Vercel API 创建站点
# -----------------------------
def create_vercel_site(repo_url: str, site_name: Optional[str] = None) -> Optional[str]:
    """创建Vercel站点"""
    if not VERCEL_TOKEN:
        logger.warning("VERCEL_TOKEN not found in environment variables")
        return None
        
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}", "Content-Type": "application/json"}
    
    # 从repo_url解析仓库信息
    try:
        repo_parts = repo_url.rstrip('.git').split('/')
        repo_name = repo_parts[-1]
        repo_owner = repo_parts[-2]
    except IndexError:
        logger.error(f"Invalid repo URL format: {repo_url}")
        return None
    
    data = {
        "name": site_name or f"sat-{int(time.time())}-{random.randint(1000, 9999)}",
        "gitRepository": {
            "type": "github",
            "repo": f"{repo_owner}/{repo_name}"
        }
    }
    
    result = make_api_request("https://api.vercel.com/v9/projects", headers, data)
    if result:
        # Vercel返回的URL格式可能不同
        site_url = result.get("alias", [{}])[0].get("domain") if result.get("alias") else f"{result.get('name')}.vercel.app"
        if site_url and not site_url.startswith('http'):
            site_url = f"https://{site_url}"
        logger.info(f"Vercel site created: {site_url}")
        return site_url
    else:
        logger.error("Failed to create Vercel site")
        return None

# -----------------------------
# Helper: 生成站点内容
# -----------------------------
def create_site_content(articles: List[Dict[str, str]], cross_links: List[str]) -> Dict[str, str]:
    """生成站点的HTML内容"""
    # 生成首页
    index_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <title>文章列表</title>
</head>
<body>
    <h1>文章列表</h1>
    <ul>
"""
    
    files = {}
    for article in articles:
        # 生成文件名
        file_name = f"{article['id']}.html"
        index_content += f'<li><a href="{file_name}">{article["name"]}</a></li>\n'
        
        # 添加随机内部链接到文章内容
        content = article['content']
        other_articles = [a for a in articles if a['id'] != article['id']]
        if other_articles:
            num_links = min(len(other_articles), random.randint(4, 6))
            random_articles = random.sample(other_articles, num_links)
            
            footer_links = "\n<footer><h3>相关阅读</h3>\n<ul>"
            for ra in random_articles:
                footer_links += f'<li><a href="{ra["id"]}.html">{ra["name"]}</a></li>\n'
            footer_links += "</ul></footer>\n"
            
            # 在 </body> 标签前插入链接
            content = content.replace("</body>", f"{footer_links}</body>")
        
        files[file_name] = content
    
    index_content += "</ul>\n</body>\n</html>"
    files['index.html'] = index_content
    
    return files

# -----------------------------
# Helper: 生成 sitemap
# -----------------------------
def generate_sitemap(netlify_urls: List[str], vercel_urls: List[str], output_file: Path):
    """生成站点地图"""
    try:
        html_content = f"""<!DOCTYPE html>
<html lang='zh-CN'>
<head>
    <meta charset='UTF-8'>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>卫星站点地图</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .site-group {{ margin-bottom: 20px; }}
        .site-group h2 {{ color: #333; border-bottom: 2px solid #eee; }}
        ul {{ list-style-type: none; padding: 0; }}
        li {{ margin: 5px 0; }}
        a {{ text-decoration: none; color: #0066cc; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <h1>卫星站点地图</h1>
    <p>生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
    
    <div class="site-group">
        <h2>Netlify 站点 ({len(netlify_urls)}个)</h2>
        <ul>
"""
        for url in netlify_urls:
            html_content += f"            <li><a href='{url}' target='_blank'>{url}</a></li>\n"
        
        html_content += f"""        </ul>
    </div>
    
    <div class="site-group">
        <h2>Vercel 站点 ({len(vercel_urls)}个)</h2>
        <ul>
"""
        for url in vercel_urls:
            html_content += f"            <li><a href='{url}' target='_blank'>{url}</a></li>\n"
        
        html_content += """        </ul>
    </div>
</body>
</html>"""
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Sitemap generated: {output_file}")
        
    except Exception as e:
        logger.error(f"Error generating sitemap: {e}")

# -----------------------------
# Helper: 验证配置
# -----------------------------
def validate_config() -> bool:
    """验证配置是否正确"""
    errors = []
    
    # 检查 Google Drive 配置
    if service is None:
        errors.append("Google Drive 服务初始化失败，请确保 GDRIVE_SERVICE_ACCOUNT 环境变量已正确配置")
    
    folder_id = os.environ.get("GDRIVE_FOLDER_ID")
    if not folder_id:
        errors.append("未找到 GDRIVE_FOLDER_ID 环境变量，请在 GitHub Repository secrets 中设置")
    
    if not NETLIFY_TOKEN:
        errors.append("NETLIFY_TOKEN not set")
    
    if not VERCEL_TOKEN:
        errors.append("VERCEL_TOKEN not set")
    
    if GITHUB_USERNAME == "YOUR_USERNAME":
        errors.append("GITHUB_USERNAME not properly configured")
    
    if errors:
        for error in errors:
            logger.error(error)
        return False
    
    return True

# -----------------------------
# Helper: 清理临时目录
# -----------------------------
def cleanup_repos():
    """清理之前创建的临时仓库目录"""
    for i in range(SATELLITE_COUNT):
        repo_path = Path(f"./sat_repo_{i+1}")
        if repo_path.exists():
            try:
                shutil.rmtree(repo_path)
                logger.info(f"Cleaned up {repo_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup {repo_path}: {e}")

# -----------------------------
# 主逻辑
# -----------------------------
def main():
    """主执行函数"""
    logger.info("Starting satellite sites deployment...")
    
    # 验证配置
    if not validate_config():
        logger.error("Configuration validation failed. Please check your settings.")
        return False
    
    # 清理旧的临时目录
    cleanup_repos()
    
    # 加载数据
    cross_links = load_cross_links(CROSS_PLATFORM_LINKS_FILE)
    
    # 从 Google Drive 获取文章
    articles = load_articles_from_drive()
    if not articles:
        logger.error("未能从 Google Drive 获取到任何文章")
        return False
    
    logger.info(f"成功获取 {len(articles)} 篇文章")
    logger.info(f"加载了 {len(cross_links)} 个跨平台链接")
    
    netlify_urls = []
    vercel_urls = []
    successful_deployments = 0

    # 从配置文件读取卫星站数量
    logger.info(f"将创建 {config.satellite_count} 个卫星站")

    for i in range(config.satellite_count):
        logger.info(f"处理卫星站 {i+1}/{config.satellite_count}")
        
        # 生成仓库名
        repo_name = generate_repo_name(config.repo_prefix)
        
        # 创建仓库并添加内容
        repo_url = create_repo_with_content(repo_name, articles, cross_links)
        if not repo_url:
            logger.error(f"创建仓库 {repo_name} 失败")
            continue
            
        # 部署到 Netlify 或 Vercel
        if i % 2 == 0 and NETLIFY_TOKEN:
            site_url = create_netlify_site(repo_name, f"{config.github_username}/{repo_name}")
            if site_url:
                netlify_urls.append(site_url)
                successful_deployments += 1
        elif VERCEL_TOKEN:
            site_url = create_vercel_site(repo_name, f"{config.github_username}/{repo_name}")
            if site_url:
                vercel_urls.append(site_url)
                successful_deployments += 1
        time.sleep(2)  # 添加延迟避免API限制

    # 所有卫星站部署完成，生成站点地图
    if successful_deployments > 0:
        sitemap_file = Path("sitemap.html")
        generate_sitemap(netlify_urls, vercel_urls, sitemap_file)
        logger.info(f"Successfully deployed {successful_deployments} satellite sites")
        return True
    else:
        logger.error("No satellite sites were deployed successfully")
        return False
    if netlify_urls or vercel_urls:
        generate_sitemap(netlify_urls, vercel_urls, Path("satellite_sitemap.html"))
    
    logger.info(f"Deployment completed! Successfully deployed {successful_deployments}/{SATELLITE_COUNT} satellites")
    logger.info(f"Netlify sites: {len(netlify_urls)}, Vercel sites: {len(vercel_urls)}")
    
    return successful_deployments > 0

if __name__ == "__main__":
    # 创建示例配置文件（如果不存在）
    create_example_config()
    
    # 加载配置
    config = load_platform_config()
    NETLIFY_TOKEN = config.netlify_token
    VERCEL_TOKEN = config.vercel_token
    GITHUB_USERNAME = config.github_username
    SATELLITE_COUNT = config.satellite_count
    
    success = main()
    if not success:
        exit(1)
