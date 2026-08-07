import os
import json
import subprocess
import urllib.request
import urllib.error

# 문서 뼈대 정의
HEADERS = {
    "raw": "# 00_원문보관 2026-07\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n━━━━━━━━━━━━━━━━━━\n",
    "반도체사이클": "# 10_테마 반도체사이클\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n복습: 새 항목이 생기면 문서를 위에서부터 훑어 읽고 ▶ 💭에 내 생각을 기록할 것.\n━━━━━━━━━━━━━━━━━━\n",
    "시장심리_수급": "# 10_테마 시장심리·수급\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n복습: 새 항목이 생기면 문서를 위에서부터 훑어 읽고 ▶ 💭에 내 생각을 기록할 것.\n━━━━━━━━━━━━━━━━━━\n",
    "환율_매크로": "# 10_테마 환율·매크로\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n복습: 새 항목이 생기면 문서를 위에서부터 훑어 읽고 ▶ 💭에 내 생각을 기록할 것.\n━━━━━━━━━━━━━━━━━━\n",
    "한화에어로스페이스": "# 20_종목 한화에어로스페이스\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n복습: 새 항목이 생기면 문서를 위에서부터 훑어 읽고 ▶ 💭에 내 생각을 기록할 것.\n━━━━━━━━━━━━━━━━━━\n",
    "에이유브랜즈": "# 20_종목 에이유브랜즈\n위에서 아래로 시간순 누적. 새 항목은 맨 아래 추가.\n복습: 새 항목이 생기면 문서를 위에서부터 훑어 읽고 ▶ 💭에 내 생각을 기록할 것.\n━━━━━━━━━━━━━━━━━━\n"
}

def get_fresh_access_token():
    try:
        # Node.js 스크립트를 기동하여 캐싱된 master key 기반으로 토큰 복호화 실행
        decrypt_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'decrypt_token.js')
        res = subprocess.run(["node", decrypt_script], capture_output=True, text=True, check=True)
        tokens = json.loads(res.stdout.strip())
        return tokens.get("main-account", {}).get("token", {}).get("accessToken")
    except Exception as e:
        print(f"Error fetching decrypted token: {e}")
        return None

def update_google_doc(doc_id, header, text_content, access_token):
    # 1. 문서 정보 가져와서 끝 인덱스 확인
    get_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {access_token}"})
    
    try:
        with urllib.request.urlopen(req) as res:
            doc_data = json.loads(res.read().decode('utf-8'))
            body = doc_data.get("body", {})
            end_index = body.get("content", [])[-1].get("endIndex", 1)
    except Exception as e:
        print(f"Error fetching doc {doc_id}: {e}")
        return False
        
    # 2. 업데이트 요청 구성
    requests = []
    
    # 2-1. 기존 본문이 존재하면 전체 삭제
    if end_index > 2:
        requests.append({
            "deleteContentRange": {
                "range": {
                    "startIndex": 1,
                    "endIndex": end_index - 1
                }
            }
        })
        
    # 2-2. 신규 텍스트 기입 (뼈대 머리말 + 실제 텍스트)
    full_text = header + text_content
    requests.append({
        "insertText": {
            "location": {
                "index": 1
            },
            "text": full_text
        }
    })
    
    update_url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
    body_data = json.dumps({"requests": requests}).encode('utf-8')
    
    update_req = urllib.request.Request(
        update_url, 
        data=body_data, 
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    )
    
    try:
        with urllib.request.urlopen(update_req) as res:
            print(f"Successfully updated document {doc_id}")
            return True
    except urllib.error.HTTPError as he:
        print(f"HTTP Error updating {doc_id}: {he.code} - {he.read().decode('utf-8', errors='ignore')}")
        return False
    except Exception as e:
        print(f"Error updating doc {doc_id}: {e}")
        return False

def main():
    access_token = get_fresh_access_token()
    if not access_token:
        print("Failed to get fresh access token.")
        return
        
    doc_mappings = {
        "raw": "17b8iLt_KxjWZX7On9hnPXCLZtm6fpT8GBgYb_X85PHY",
        "반도체사이클": "1UeI5DBboBk-Tedvu4G6ucOuZdxJAtkRg55N_VMV4U-s",
        "시장심리_수급": "1k2vvUvzgl6iW08MGbY9oehc2zBnKtPAi-z6OqrwaSoA",
        "환율_매크로": "18lU0RmfkofcRuosdPn664x4AG8DSO12gvsDqLUi2eKE",
        "한화에어로스페이스": "1F2YJCAtqznASlhY9351UA0qX-81bw2hbrqQM6qrrd9I",
        "에이유브랜즈": "1cUfOHw0OAV0Wwpi5qM4aR3BSi2-H76aV7F8L9RtK-ek"
    }
    
    formatted_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'formatted_outputs')
    
    for key, doc_id in doc_mappings.items():
        file_path = os.path.join(formatted_dir, f"{key}.txt")
        if not os.path.exists(file_path):
            print(f"Skipping key (File not found)", flush=True)
            continue
            
        with open(file_path, 'r', encoding='utf-8') as f:
            text_content = f.read()
            
        print(f"Updating Doc ID: {doc_id} (chars: {len(text_content)})", flush=True)
        update_google_doc(doc_id, HEADERS[key], text_content, access_token)

if __name__ == '__main__':
    main()
