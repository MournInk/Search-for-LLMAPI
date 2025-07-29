from flask import *
import json, json5, logging, os, re, requests, time, utils
from functools import wraps

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

os.chdir(os.path.dirname(__file__))

# Load configuration with error handling
try:
    config = json5.load(open('config.json5', 'r', encoding='utf-8'))
except FileNotFoundError:
    logger.error("Configuration file 'config.json5' not found!")
    exit(1)
except json5.Json5Exception as e:
    logger.error(f"Error parsing configuration file: {e}")
    exit(1)

app = Flask(__name__)

# Security configurations
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size

def validate_auth():
    """Validate authentication and return error response if invalid"""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({'error': 'Missing Authorization header'}), 401
    
    try:
        auth_type, password = auth_header.split(' ', 1)
        if auth_type.lower() != 'bearer':
            return jsonify({'error': 'Invalid authorization type. Use Bearer.'}), 401
    except ValueError:
        return jsonify({'error': 'Invalid Authorization header format'}), 401
    
    if not config['auth']['access_password']:
        return jsonify({'error': 'Server access password not configured'}), 500
    
    if password != config['auth']['access_password']:
        logger.warning(f"Unauthorized access attempt from {request.remote_addr}")
        return jsonify({'error': 'Unauthorized'}), 401
    
    return None

def validate_request_data(data):
    """Validate incoming request data"""
    if not data:
        return "Request body is required", 400
    
    if 'messages' not in data:
        return "Missing 'messages' field", 400
    
    if not isinstance(data['messages'], list) or len(data['messages']) == 0:
        return "Messages must be a non-empty list", 400
    
    last_message = data['messages'][-1]
    if 'content' not in last_message:
        return "Last message must contain 'content'", 400
    
    content = last_message['content']
    if not content or len(content.strip()) == 0:
        return "Message content cannot be empty", 400
    
    max_length = config.get('limits', {}).get('max_message_length', 8192)
    if len(content) > max_length:
        return f"Message content too long (max {max_length} characters)", 400
    
    return None, None

@app.route('/v1/chat/completions', methods = ['POST'])
def completions():
    # Validate authentication
    auth_error = validate_auth()
    if auth_error:
        return auth_error
    
    # Get and validate request data
    try:
        data = request.get_json()
    except Exception as e:
        logger.error(f"Error parsing JSON: {e}")
        return jsonify({'error': 'Invalid JSON format'}), 400
    
    # Validate request data
    validation_error, status_code = validate_request_data(data)
    if validation_error:
        return jsonify({'error': validation_error}), status_code
    
    # Set up headers for external API
    headers = {
        'Authorization': f'Bearer {config["api"]["secret_key"]}',
        'Content-Type': 'application/json'
    }
    
    data['stream'] = True
    
    def generate():
        try:
            user_question = data['messages'][-1]['content']
            # Sanitize user input - remove potentially harmful content
            user_question = re.sub(r'[<>"\'\&]', '', user_question)
            
            payload = data.copy()
            payload['messages'] = [{
                'role': 'user',
                'content': f'{user_question}\n为了回答这个问题, 你需要搜索哪些内容？（搜索引擎输入格式，一行一个，仅(MUST)可(ONLY)问题, 至多 3 个）\n当前时间: {time.strftime("%Y年%m月%d日 %H:%M:%S")}'
            }]
            
            # Make request with timeout
            response = requests.post(
                url = config['api']['base_url'] + '/chat/completions',
                json = payload,
                headers = headers,
                stream = True,
                timeout = config.get('timeout', {}).get('api_request', 30)
            )
            response.raise_for_status()
            
            reasoning = False
            finish_reasoning = False
            search_questions = ""
            
            for line in response.iter_lines():
                if not line: 
                    continue
                json_str = line.decode('utf-8').replace('data: ', '')
                if json_str == '[DONE]': 
                    break
                if json_str == ': keep-alive': 
                    continue
                try:
                    json_ = json.loads(json_str)
                except json.JSONDecodeError:
                    yield f'data: {json_str}\n\n'
                    continue
                    
                if 'choices' not in json_: 
                    continue
                    
                json_['choices'][0]['finish_reason'] = None
                reasoning_content = json_['choices'][0]['delta'].get('reasoning_content', '')
                content = json_['choices'][0]['delta'].get('content', '')
                
                if not reasoning:
                    reasoning = True
                    json_['choices'][0]['delta']['reasoning_content'] = "**思考搜索内容中：**\n" + reasoning_content
                if content:
                    search_questions += content
                    if not finish_reasoning:
                        finish_reasoning = True
                        content = "\n**思考完成，即将搜索以下内容：**\n" + content
                    json_['choices'][0]['delta']['content'] = None
                    json_['choices'][0]['delta']['reasoning_content'] = content
                yield f'data: {json.dumps(json_, ensure_ascii=False)}\n\n'
            
            # Process search questions
            search_questions = [x.strip() for x in search_questions.split('\n') if x.strip()]
            max_questions = config.get('limits', {}).get('max_search_questions', 3)
            search_questions = search_questions[:max_questions]
            
            result_id = 1
            search_context = ''
            
            for search_question in search_questions:
                # Sanitize search question
                search_question = re.sub(r'[<>"\'\&]', '', search_question)
                if not search_question:
                    continue
                    
                search_context += f"**{search_question} 的搜索结果：**\n"
                
                try:
                    search_results = utils.bocha_search(
                        api_key = config['api']['bocha_secret_key'],
                        base_url = config['api']['bocha_base_url'],
                        query = search_question
                    )
                except Exception as e:
                    logger.error(f"Error while searching for {search_question}: {e}")
                    continue
                
                search_results_summary = []
                if 'data' in search_results and 'webPages' in search_results['data']:
                    web_pages = search_results['data']['webPages']['value']
                    max_pages = config.get('limits', {}).get('max_pages_per_search', 5)
                    
                    for page in web_pages[:max_pages]:
                        try:
                            timeout = config.get('timeout', {}).get('web_fetch', 10)
                            max_length = config.get('limits', {}).get('max_content_length', 2000)
                            text = utils.get_text_from_url(page['displayUrl'], timeout, max_length)
                            if text:
                                search_context += f'来源：[{result_id}] {page["displayUrl"]}\n内容：{text}\n---\n'
                                search_results_summary.append(f'[{result_id}] [{page["name"]}]({page["displayUrl"]})')
                                result_id += 1
                        except Exception as e:
                            logger.error(f"Error processing page {page.get('displayUrl', 'unknown')}: {e}")
                            continue
                
                result = {"choices": [{"delta": {
                    "content": None,
                    "reasoning_content": f"\n\n**{search_question} 的搜索结果：**\n" + "\n".join(search_results_summary),
                }}]}
                yield f'data: {json.dumps(result, ensure_ascii=False)}\n\n'
            
            yield f'data: {json.dumps({"choices": [{"delta": {"reasoning_content": "\n\n**搜索完成，开始思考并回答：**\n\n"}}]}, ensure_ascii=False)}\n\n'
            
            # Final response
            payload = data.copy()
            payload['messages'].append({
                'role': 'assistant',
                'content': f'我需要搜索以下内容：{";".join(search_questions)}。请向我提供相关文本以帮助我回答用户问题。'
            })
            payload['messages'].append({
                'role': 'user',
                'content':  f'下面是你需要的搜索结果，请根据搜索结果回答上面用户的问题。\n'\
                            f'回答时如果数据来源于下面的一个或多个搜索结果，你须要(MUST)使用[结果编号]进行标记。\n'\
                            f'例如：这是你的回答。[1][7]\n'\
                            f'下面是搜索结果：\n{search_context}'
            })
            
            response = requests.post(
                url = config['api']['base_url'] + '/chat/completions',
                json = payload,
                headers = headers,
                stream = True,
                timeout = config.get('timeout', {}).get('api_request', 30)
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if not line: 
                    continue
                json_str = line.decode('utf-8').replace('data: ', '')
                if json_str == '[DONE]': 
                    break
                if json_str == ': keep-alive': 
                    continue
                yield f'data: {json_str}\n\n'
            yield "data: [DONE]\n\n"
            
        except requests.exceptions.Timeout:
            logger.error("Request timeout")
            yield f'data: {json.dumps({"error": "Request timeout"}, ensure_ascii=False)}\n\n'
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            yield f'data: {json.dumps({"error": "External API error"}, ensure_ascii=False)}\n\n'
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            yield f'data: {json.dumps({"error": "Internal server error"}, ensure_ascii=False)}\n\n'
    
    return Response(
        stream_with_context(generate()),
        mimetype = 'text/event-stream'
    )
    
if __name__ == '__main__':
    # Validate configuration
    if not config['auth']['access_password']:
        logger.warning("Access password is not set! Please configure a strong password in config.json5")
    
    # Get server configuration with secure defaults
    host = config['server'].get('address', '127.0.0.1')  # Default to localhost only
    port = config['server'].get('port', 11451)
    debug = config['server'].get('debug', False)
    
    if host == '0.0.0.0':
        logger.warning("Server is configured to listen on all interfaces (0.0.0.0). This may pose security risks.")
    
    logger.info(f"Starting server on {host}:{port}")
    
    app.run(
        host=host,
        port=port,
        debug=debug
    )