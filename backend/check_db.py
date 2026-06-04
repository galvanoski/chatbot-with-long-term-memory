import sqlite3
conn = sqlite3.connect(r'C:\Users\CP\Downloads\AI enginiering\Modulo 3\chatbot with long-term memory\threads.db')
conn.row_factory = sqlite3.Row

try:
    rows = conn.execute('SELECT id, user_id, status, title FROM chat_threads').fetchall()
    for r in rows:
        print(f"  id={r['id']}  user={r['user_id'][:40]}  status={r['status']}")
    print(f"Total: {len(rows)} threads")
except Exception as e:
    print(f"chat_threads error: {e}")

tid = '22524d77-71a3-4a05-b5d2-4106c8f12bb9'
row = conn.execute('SELECT id, user_id, status FROM chat_threads WHERE id=?', (tid,)).fetchone()
if row:
    print(f"\nFound: {row['id']} user={row['user_id']} status={row['status']}")
else:
    print(f"\nThread {tid} NOT FOUND in chat_threads")

# Check checkpoints
try:
    rows = conn.execute('SELECT thread_id FROM checkpoints LIMIT 10').fetchall()
    print(f"\nCheckpoints ({len(rows)}):")
    for r in rows:
        print(f"  {r['thread_id']}")
except Exception as e:
    print(f"checkpoints error: {e}")
