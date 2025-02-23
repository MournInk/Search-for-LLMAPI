from bs4 import BeautifulSoup
import configparser, json, logging, re, requests
def bocha_search(api_key: str, base_url: str, **kwargs) -> dict:
    """
    通过博查的网络搜索接口进行搜索
    :param api_key: 博查的 API Key
    :param base_url: 博查的 API 地址
    :param kwargs: 搜索参数，参考博查的 Web Searching API 文档
    :return: 搜索结果
    """
    response = requests.post(
        url = base_url + "/web-search",
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        },
        data = json.dumps(kwargs)
    ) 
    return response.json()
def get_text_from_url(url: str) -> str | None:
    """
    从网页中提取文本
    :param url: 网页地址
    :return: 网页文本
    """
    tags = ['head', 'script', 'style', 'noscript', 'meta', 'link', 'header', 'footer', 'nav']
    classes = ['header', 'footer', 'comments', 'recommend', 'search']
    def should_remove(tag):
        if tag.name in tags: return True
        if 'class' in tag.attrs:
            for cls in tag['class']:
                if cls in classes: return True
                for _cls in classes:
                    if _cls in cls.lower(): return True
        return False
    try:
        response = requests.get(url, headers = {'User-Agent': 'Chrome/114.5.1.4'})
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        for element in soup.find_all(should_remove):
            element.decompose()
        text = soup.get_text(separator='\n')
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        content = '\n'.join(chunk for chunk in chunks if chunk)
        return content
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return None