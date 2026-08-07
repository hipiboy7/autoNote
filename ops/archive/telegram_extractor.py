import os
import sys
import json
import asyncio
import re
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# D:\autoNote\telegram-mcp\.env 파일에서 환경 변수 로드
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'telegram-mcp', '.env')
load_dotenv(env_path)

# Telegram 설정
API_ID = int(os.getenv("TELEGRAM_API_ID", 0))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")
CHANNEL_ID = -1004421517798  # 피터케이 채널

# agy.exe 경로 지정
AGY_PATH = r"C:\Users\SYSADMIN\AppData\Local\agy\bin\agy.exe"

if not API_ID or not API_HASH or not SESSION_STRING:
    print("Error: Telegram API variables must be set in .env", file=sys.stderr)
    sys.exit(1)

async def extract_chunk_research_data(chunk_messages, model_name="claude-3-5-sonnet"):
    """여러 메시지를 하나의 프롬프트로 묶어 agy.exe를 통해 일괄 분석"""
    messages_text = ""
    for msg in chunk_messages:
        messages_text += f"=== MESSAGE_ID: {msg['id']} | DATE: {msg['date']} ===\n{msg['text']}\n\n"

    prompt = f"""
역할: 전문 주식 투자 리서처.
아래 제공된 텔레그램 메시지 목록을 분석하여, 각 메시지별로 리서치 가치가 있는 글(핵심 주장과 논리가 들어있는 글)을 추출하고 규격화된 JSON Array 형식으로 변환해주세요.

[분석 및 분류 규칙]
1. 테마 분류 (theme): 아래 4개 테마 중 가장 주된 테마 1개로 분류하세요. (하나에 매핑되지 않거나 모호한 리서치는 null 지정 가능)
   - 반도체사이클
   - 시장심리·수급
   - 환율·매크로
   - 금리
2. 종목 분석 (stock): 구체적으로 분석된 기업명이 있으면 기업명을 쓰세요. 없으면 null로 지정하세요. (예: SK하이닉스, 한화에어로스페이스, 에이유브랜즈 등)
3. 리서치 판단 가치 판정 (is_research): 단순 공지, 단순 뉴스 헤드라인 공유, 리서처의 분석/논리가 없는 짧은 잡담 등은 false로 지정하고, 핵심 판단과 근거가 담긴 글은 true로 지정하세요.
4. 출력은 반드시 아래의 키값을 가지는 JSON 오브젝트들의 Array 형식이어야 합니다. 마크다운 백틱(```json)이나 설명 텍스트 없이 오직 순수 JSON 데이터 자체만 출력해야 합니다.
[
  {{
    "message_id": 메시지ID(integer),
    "is_research": true 또는 false,
    "theme": "테마명 또는 null",
    "stock": "기업명 또는 null",
    "key_argument": "저자가 말하려는 핵심 주장 (1~2문장)",
    "evidence": "저자가 제시한 숫자, 데이터, 사실, 관찰 내용",
    "logic": "판단 논리 (저자가 왜 그렇게 판단했는지, 어떤 틀로 해석했는지)",
    "risk": "저자 언급 리스크 혹은 미언급이지만 고려해볼 수 있는 반대 시나리오",
    "question": "이 글의 논리를 검증할 수 있는 날카로운 생각해볼 질문 1개"
  }},
  ...
]

[메시지 목록]
{messages_text}
"""
    try:
        cmd = [
            AGY_PATH,
            "--model", model_name,
            "--print", prompt,
            "--dangerously-skip-permissions"
        ]
        
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            startupinfo=startupinfo
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            err_msg = stderr.decode('utf-8', errors='ignore')
            print(f"Agy execution error: {err_msg}", file=sys.stderr)
            return []
            
        content = stdout.decode('utf-8', errors='ignore').strip()
        
        # 출력 결과에서 JSON Array 매칭
        json_match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
            
        print(f"Error parsing JSON Array from output chunk: {content}", file=sys.stderr)
        return []
        
    except Exception as e:
        print(f"Subprocess call error for chunk: {e}", file=sys.stderr)
        return []

async def main():
    import argparse
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    parser = argparse.ArgumentParser()
    parser.add_argument('--all', action='store_true', help='Process all messages from history')
    parser.add_argument('--model', default='claude-3-5-sonnet', help='LLM model to use (claude-3-5-sonnet, gpt-4o)')
    parser.add_argument('--chunk-size', type=int, default=20, help='Number of messages to analyze per agy call')
    args = parser.parse_args()

    state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'state.json')
    last_message_id = 0
    
    if not args.all and os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                last_message_id = state["channels"].get(str(CHANNEL_ID), {}).get("last_message_id", 0)
        except Exception as e:
            print(f"Warning reading state.json: {e}", file=sys.stderr)

    print(f"Connecting to Telegram... (last_message_id = {last_message_id if not args.all else 0})", file=sys.stderr)
    
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    raw_messages = []
    min_id = 0 if args.all else last_message_id
    async for message in client.iter_messages(CHANNEL_ID, min_id=min_id, reverse=True):
        if message.text:
            text = message.text.strip()
            # 1차 필터링: 너무 짧거나 단순 링크는 대상에서 제외
            if len(text) < 40 and ("http" in text or text.count('\n') == 0):
                continue
            
            raw_messages.append({
                "id": message.id,
                "date": message.date.strftime("%Y-%m-%d %H:%M:%S KST"),
                "text": text
            })
            
    print(f"Found {len(raw_messages)} filtered messages to process.", file=sys.stderr)
    
    extracted_data = []
    for idx in range(0, len(raw_messages), args.chunk_size):
        chunk = raw_messages[idx : idx + args.chunk_size]
        print(f"Analyzing chunk {idx // args.chunk_size + 1} ({len(chunk)} messages) via agy.exe with {args.model}...", file=sys.stderr)
        
        chunk_results = await extract_chunk_research_data(chunk, model_name=args.model)
        
        for item in chunk_results:
            msg_id = item.get("message_id")
            orig_msg = next((m for m in chunk if m["id"] == msg_id), None)
            if orig_msg and item.get("is_research"):
                item["raw_content"] = orig_msg["text"]
                item["date"] = orig_msg["date"].split(" ")[0]
                extracted_data.append(item)
                print(f"  -> Added message {msg_id} as research: Theme: {item.get('theme')}, Stock: {item.get('stock')}", file=sys.stderr)
                
        await asyncio.sleep(1)
            
    output_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'extracted_messages.json')
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(extracted_data, f, ensure_ascii=False, indent=2)
        
    print(f"Extraction completed. {len(extracted_data)} research messages saved to {output_file}", file=sys.stderr)
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
