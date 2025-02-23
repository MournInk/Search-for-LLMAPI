from flask import *
import json, json5, logging, os, re, requests, time, utils
os.chdir(os.path.dirname(__file__))
config = json5.load(open('configs.json5', 'r', encoding='utf-8'))
app = Flask(__name__)
@app.route('/v1/chat/completions', methods = ['POST'])
def completions():
    password = request.headers.get('Authorization').split()[1]
    if password != config['auth']['access_password']:
        return jsonify({'error': 'Unauthorized'}), 401
    headers = {
        'Authorization': f'Bearer {config["api"]["secret_key"]}',
        'Content-Type': 'application/json'
    }
    data = request.get_json()
    data['stream'] = True
    def generate():
        user_question = data['messages'][-1]['content']
        payload = data
        payload['messages'] = [{
            'role': 'user',
            'content': f'{user_question}\n为了回答这个问题, 你需要搜索哪些内容？（搜索引擎输入格式，一行一个，仅(MUST)可(ONLY)问题, 至多 3 个）\n当前时间: {time.strftime("%Y年%m月%d日 %H:%M:%S")}'
        }]
        response = requests.post(
            url = config['api']['base_url'] + '/chat/completions',
            json = payload,
            headers = headers,
            stream = True
        )
        reasoning = False
        finish_reasoning = False
        search_questions = ""
        for line in response.iter_lines():
            if not line: continue
            json_str = line.decode('utf-8').replace('data: ', '')
            if json_str == '[DONE]': break
            if json_str == ': keep-alive': continue
            try:
                json_ = json.loads(json_str)
            except:
                yield f'data: {json_str}\n\n'
                continue
            if 'choices' not in json_: continue
            json_['choices'][0]['finish_reason'] = None
            reasoning_content = json_['choices'][0]['delta']['reasoning_content']
            content = json_['choices'][0]['delta']['content']
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
        search_questions = [x for x in search_questions.split('\n') if x]
        result_id = 1
        search_context = ''
        for search_question in search_questions:
            search_context += f"**“{search_question}” 的搜索结果：**\n"
            try:
                search_results = utils.bocha_search(
                    api_key = config['api']['bocha_secret_key'],
                    base_url = config['api']['bocha_base_url'],
                    query = search_question
                )
            except:
                logging.error(f"Error while searching for “{search_question}”")
                continue
            search_results_summary = []
            if 'data' in search_results and 'webPages' in search_results['data']:
                web_pages = search_results['data']['webPages']['value']
                for page in web_pages:
                    text = utils.get_text_from_url(page['displayUrl'])
                    if text:
                        search_context += f'来源：[{result_id}] {page["displayUrl"]}\n内容：{text}\n---\n'
                        search_results_summary.append(f'[{result_id}] [{page["name"]}]({page["displayUrl"]})')
                        result_id += 1
            result = {"choices": [{"delta": {
                "content": None,
                "reasoning_content": f"\n\n**“{search_question}” 的搜索结果：**\n" + "\n".join(search_results_summary),
            }}]}
            yield f'data: {json.dumps(result, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({"choices": [{"delta": {"reasoning_content": "\n\n**搜索完成，开始思考并回答：**\n\n"}}]}, ensure_ascii=False)}\n\n'
        payload = data
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
            json = data,
            headers = headers,
            stream = True
        )
        for line in response.iter_lines():
            if not line: continue
            json_str = line.decode('utf-8').replace('data: ', '')
            if json_str == '[DONE]': break
            if json_str == ': keep-alive': continue
            yield f'data: {json_str}\n\n'
        yield "data: [DONE]\n\n"
    return Response(
        stream_with_context(generate()),
        mimetype = 'text/event-stream'
    )
app.run(
    host = config['server']['address'],
    port = config['server']['port']
)