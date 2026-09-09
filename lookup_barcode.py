# בדיקת פריט לפי ברקוד — מדפיס את כל העמודות מ-ItemMain (כולל תאריכי הקמה/עדכון)
# שימוש: BARCODE=26S3064L20 python lookup_barcode.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sync import DATABASES, _connect

BARCODE = os.environ.get('BARCODE', '').strip()
if not BARCODE:
    print("BARCODE לא הוגדר")
    sys.exit(1)

CORE = BARCODE[:7]  # למשל 26S3064 — חיפוש רחב בלי המידה

def dump_item(cur, item_id):
    cur.execute("SELECT * FROM ItemMain WHERE ItemID = %s", (item_id,))
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        print(f"  --- ItemMain (ItemID={item_id}) ---")
        for c, v in zip(cols, row):
            if v is not None and str(v).strip() != '':
                print(f"    {c}: {v}")
    try:
        cur.execute("""
            SELECT MIN(t.TransactionTime) FROM TransactionEntry te
            JOIN [Transaction] t ON te.TransactionID = t.TransactionID
            JOIN ItemStore ist ON ist.ItemStoreID = te.ItemStoreID
            WHERE ist.ItemID = %s
        """, (item_id,))
        print(f"  תנועה ראשונה בקופה: {cur.fetchone()[0]}")
    except Exception as e:
        print(f"  (שליפת תנועה ראשונה נכשלה: {e!r})")

for cfg in DATABASES:
    label = cfg.get('label') or 'DB'
    print(f"\n===== מסד: {label} =====")
    try:
        conn = _connect(cfg)
    except Exception as e:
        print(f"  חיבור נכשל: {e!r}")
        continue
    cur = conn.cursor()

    # אילו טבלאות מכילות עמודת ברקוד בכלל
    cur.execute("""
        SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
        WHERE COLUMN_NAME LIKE '%arcode%' OR COLUMN_NAME LIKE '%Barkod%'
    """)
    tables = cur.fetchall()
    print("  עמודות ברקוד בסכמה:", ', '.join(f"{t}.{c}" for t, c in tables))

    found_ids = set()

    # התאמה מדויקת + LIKE על ItemMain (ברקוד ומק"ט/דגם), בלי סינון סטטוס
    cur.execute("""
        SELECT ItemID, Name, BarcodeNumber, ModelNumber, Status FROM ItemMain
        WHERE BarcodeNumber = %s OR ModelNumber = %s
           OR BarcodeNumber LIKE %s OR ModelNumber LIKE %s
    """, (BARCODE, BARCODE, '%' + CORE + '%', '%' + CORE + '%'))
    rows = cur.fetchall()
    print(f"  ItemMain: {len(rows)} התאמות ל-{BARCODE} / {CORE}")
    for iid, name, bc, mn, st in rows[:30]:
        print(f"    ItemID={iid} | {name} | ברקוד={bc} | דגם={mn} | סטטוס={st}")
        found_ids.add(iid)

    # חיפוש בכל שאר טבלאות הברקוד (למשל טבלת ברקודים משניים)
    for t, c in tables:
        if t == 'ItemMain':
            continue
        try:
            cur.execute(f"SELECT TOP 10 * FROM [{t}] WHERE [{c}] = %s OR [{c}] LIKE %s",
                        (BARCODE, '%' + CORE + '%'))
            trows = cur.fetchall()
            if trows:
                tcols = [d[0] for d in cur.description]
                print(f"  {t}: {len(trows)} התאמות")
                for r in trows:
                    print("    " + ' | '.join(f"{cc}={vv}" for cc, vv in zip(tcols, r) if vv is not None))
                    if 'ItemID' in tcols:
                        found_ids.add(r[tcols.index('ItemID')])
        except Exception as e:
            print(f"  ({t}.{c}: {e!r})")

    for iid in sorted(found_ids)[:5]:
        dump_item(cur, iid)

    conn.close()
