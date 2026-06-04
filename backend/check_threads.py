import sqlite3
conn = sqlite3.connect('threads.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT id, user_id, status FROM chat_threads').fetchall()
for r in rows:
    rid = r['id']
    uid = r['user_id'][:30]
    st = r['status']
    print(f"  id={rid}  user={uid}  status={st}")
print(f"Total: {len(rows)} threads")

# Check if the specific thread exists
tid = '22524d77-71a3-4a05-b5d2-4106c8f12bb9'
row = conn.execute('SELECT id, user_id, status FROM chat_threads WHERE id=?', (tid,)).fetchone()
if row:
    print(f"\nFound thread: {row['id']} user={row['user_id']} status={row['status']}")
else:
    print(f"\nThread {tid} NOT FOUND in chat_threads")
