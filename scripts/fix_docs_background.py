import json
import subprocess
import urllib.request
import urllib.parse
import sys

def get_access_token():
    cmd = ["node", "D:\\autoNote\\scripts\\decrypt_token.js"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return data["main-account"]["token"]["accessToken"]

def apply_background_style(doc_id: str, access_token: str):
    url = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
    
    # 1. 문서 전체의 글자 수를 알아내기 위해 GET document 호출
    get_url = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    req_get = urllib.request.Request(get_url)
    req_get.add_header("Authorization", f"Bearer {access_token}")
    
    doc_len = 10000
    try:
        with urllib.request.urlopen(req_get) as resp:
            doc_data = json.loads(resp.read().decode('utf-8'))
            body_content = doc_data.get("body", {}).get("content", [])
            if body_content:
                doc_len = body_content[-1].get("endIndex", 10000)
    except Exception as e:
        print(f"Error fetching doc {doc_id} structure: {e}")

    # 2. updateDocumentStyle (페이지 배경색 세피아/라이트) + updateTextStyle (글자색 지정)
    body = {
        "requests": [
            {
                "updateDocumentStyle": {
                    "documentStyle": {
                        "background": {
                            "color": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.99,
                                        "green": 0.96,
                                        "blue": 0.89
                                    }
                                }
                            }
                        }
                    },
                    "fields": "background"
                }
            },
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": 1,
                        "endIndex": max(doc_len - 1, 2)
                    },
                    "textStyle": {
                        "foregroundColor": {
                            "color": {
                                "rgbColor": {
                                    "red": 0.17,
                                    "green": 0.21,
                                    "blue": 0.28
                                }
                            }
                        }
                    },
                    "fields": "foregroundColor"
                }
            }
        ]
    }
    
    req_post = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    })
    
    try:
        with urllib.request.urlopen(req_post) as resp:
            print(f"[Success] Applied Sepia/Light background to doc {doc_id}")
            return True
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')
        print(f"[Error] Failed to update background for doc {doc_id}: {e} -> {err_body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[Error] Failed to update background for doc {doc_id}: {e}", file=sys.stderr)
        return False

def main():
    token = get_access_token()
    
    target_docs = [
        ("20_종목 GS글로벌", "1EPW5TGlkQzAbbRQ2JL-PR8TjaWvGmO9sildDrsLg_LU"),
        ("20_종목 해성디에스", "1VdL4zYxir4-jVphf8VpNmJgaJsya_tDDlIdFwgZVGxE"),
        ("40_리뷰 2026-W30", "1PSk0cHaiqd5jZYbU81ew_k-bdDlBQkBXwXVvZOrhU3g"),
        ("40_리뷰 2026-W29", "1dmdM-cw8GPGaziAPiB42EF9PKmviHU8sIdjFJPcnv0k"),
        ("40_리뷰 2026-W28", "1oxSVNADOduXY6YOkqZyZcrwrNUKIrFCIzPcK6G6vdGA"),
        ("40_리뷰 2026-W27", "1hHcUuFPUmzLe5Y4kqN1OR9KSR6nB1rQQ_wdXteXcxYk"),
    ]
    
    for name, doc_id in target_docs:
        print(f"Updating background style for {name} ({doc_id})...")
        apply_background_style(doc_id, token)

if __name__ == "__main__":
    main()
