import os

def split_file(file_path, chunk_chars=4000):
    if not os.path.exists(file_path):
        return
        
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    out_dir = os.path.join(os.path.dirname(file_path), f"chunks_{base_name}")
    os.makedirs(out_dir, exist_ok=True)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    chunk_idx = 0
    for i in range(0, len(content), chunk_chars):
        chunk = content[i : i + chunk_chars]
        chunk_file = os.path.join(out_dir, f"chunk_{chunk_idx}.txt")
        with open(chunk_file, 'w', encoding='utf-8') as out_f:
            out_f.write(chunk)
        chunk_idx += 1
        
    print(f"Saved {chunk_idx} chunks", flush=True)

def main():
    base_dir = r"D:\autoNote\data\formatted_outputs"
    files_to_split = ["raw.txt", "반도체사이클.txt", "시장심리_수급.txt"]
    for f in files_to_split:
        split_file(os.path.join(base_dir, f))

if __name__ == '__main__':
    main()
