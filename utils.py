from bs4 import BeautifulSoup
import configparser, json, logging, re, requests
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

def bocha_search(api_key: str, base_url: str, **kwargs) -> dict:
    """
    通过博查的网络搜索接口进行搜索
    :param api_key: 博查的 API Key
    :param base_url: 博查的 API 地址
    :param kwargs: 搜索参数，参考博查的 Web Searching API 文档
    :return: 搜索结果
    """
    try:
        # Add timeout to the request
        response = requests.post(
            url = base_url + "/web-search",
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            data = json.dumps(kwargs),
            timeout = 10  # 10 second timeout
        ) 
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logger.error("Search request timed out")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"Search request failed: {e}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response from search API: {e}")
        raise

def is_safe_url(url: str) -> bool:
    """
    检查URL是否安全可访问
    :param url: 待检查的URL
    :return: 是否安全
    """
    try:
        parsed = urlparse(url)
        # Only allow http and https
        if parsed.scheme not in ['http', 'https']:
            return False
        # Block localhost and private IPs
        if parsed.hostname in ['localhost', '127.0.0.1', '0.0.0.0']:
            return False
        # Block private IP ranges (basic check)
        if parsed.hostname and (
            parsed.hostname.startswith('192.168.') or
            parsed.hostname.startswith('10.') or
            parsed.hostname.startswith('172.')
        ):
            return False
        return True
    except Exception:
        return False

def get_text_from_url(url: str, timeout: int = 10, max_length: int = 2000) -> str | None:
    """
    从网页中提取文本
    :param url: 网页地址
    :param timeout: 请求超时时间
    :param max_length: 最大内容长度
    :return: 网页文本
    """
    # Security check
    if not is_safe_url(url):
        logger.warning(f"Blocked unsafe URL: {url}")
        return None
    
    tags = ['head', 'script', 'style', 'noscript', 'meta', 'link', 'header', 'footer', 'nav']
    classes = ['header', 'footer', 'comments', 'recommend', 'search', 'advertisement', 'ads']
    
    def should_remove(tag):
        if tag.name in tags: 
            return True
        if 'class' in tag.attrs:
            for cls in tag['class']:
                if cls.lower() in [c.lower() for c in classes]: 
                    return True
                for _cls in classes:
                    if _cls.lower() in cls.lower(): 
                        return True
        return False
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        
        soup = BeautifulSoup(response.text, 'html.parser')
        for element in soup.find_all(should_remove):
            element.decompose()
        
        text = soup.get_text(separator='\n')
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        content = '\n'.join(chunk for chunk in chunks if chunk)
        
        # Limit content length
        if len(content) > max_length:
            content = content[:max_length] + "..."
        
        return content
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing {url}: {e}")
        return None