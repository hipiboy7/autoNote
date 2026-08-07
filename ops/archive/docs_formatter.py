import os
import json

def format_theme_entry(item):
    """테마 노트 형식으로 포맷팅"""
    source = item.get("source", "피터케이 채널(26년 하반기)")
    return f"""━━━━━━━━━━━━━━━━━━
■ {item.get('date')} | {item.get('key_argument')[:40]}...
▶ 출처: {source}
▶ 핵심 주장: {item.get('key_argument')}
▶ 근거·데이터: {item.get('evidence')}
▶ 판단 논리: {item.get('logic')}
▶ 리스크·반대 시나리오: {item.get('risk')}
▶ 💭 내 생각: 미기록
▶ ❓ 생각해볼 질문: {item.get('question')}
"""

def format_raw_entry(item):
    """원문 보관 형식으로 포맷팅"""
    source = item.get("source", "피터케이 채널(26년 하반기)")
    return f"""━━━━━━━━━━━━━━━━━━
■ {item.get('date')} | {item.get('key_argument')[:40]}...
▶ 출처: {source}
{item.get('raw_content')}
"""

def main():
    json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'extracted_messages.json')
    if not os.path.exists(json_path):
        print(f"Error: {json_path} does not exist.")
        return
        
    with open(json_path, 'r', encoding='utf-8') as f:
        items = json.load(f)
        
    print(f"Loaded {len(items)} messages to format.")
    
    # 테마별/문서별 데이터 축적
    data_by_doc = {
        "raw": "",
        "반도체사이클": "",
        "시장심리_수급": "",
        "환율_매크로": "",
        "금리": "",
        "한화에어로스페이스": "",
        "에이유브랜즈": ""
    }
    
    for item in items:
        # 1. 원문 보관 누적
        data_by_doc["raw"] += format_raw_entry(item)
        
        # 2. 테마 노트 분류 누적
        theme = item.get("theme")
        if theme == "반도체사이클":
            data_by_doc["반도체사이클"] += format_theme_entry(item)
        elif theme == "시장심리·수급" or theme == "시장심리_수급":
            data_by_doc["시장심리_수급"] += format_theme_entry(item)
        elif theme == "환율·매크로" or theme == "환율_매크로":
            data_by_doc["환율_매크로"] += format_theme_entry(item)
        elif theme == "금리":
            data_by_doc["금리"] += format_theme_entry(item)
            
        # 3. 종목 노트 분류 누적
        stock = item.get("stock")
        if stock:
            # 주식명이 한화에어로스페이스 또는 에이유브랜즈일 때만 누적
            if "한화" in stock or "에어로" in stock:
                data_by_doc["한화에어로스페이스"] += format_theme_entry(item)
            elif "에이유" in stock or "브랜즈" in stock:
                data_by_doc["에이유브랜즈"] += format_theme_entry(item)
                
    # 텍스트 파일로 출력 저장
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'formatted_outputs')
    os.makedirs(out_dir, exist_ok=True)
    
    for key, text in data_by_doc.items():
        if text.strip():
            out_file = os.path.join(out_dir, f"{key}.txt")
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write(text)
            print(f"Saved formatted output size: {len(text)} chars", flush=True)
            
if __name__ == '__main__':
    main()
