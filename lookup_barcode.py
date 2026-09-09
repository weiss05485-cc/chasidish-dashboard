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

for cfg in DATABASES:
    label = cfg.get('label') or cfg.get('name') or cfg['server']
    print(f"\n===== מסד: {label} =====")
    try:
        conn = _connect(cfg)
    except Exception as e:
        print(f"  חיבור נכשל: {e!r}")
        continue
    cur = conn.cursor()

    cur.execute("SELECT * FROM ItemMain WHERE BarcodeNumber = %s", (BARCODE,))
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    if not rows:
        print(f"  לא נמצא פריט עם ברקוד {BARCODE}")
    for row in rows:
        print(f"  --- ItemMain (ItemID={row[cols.index('ItemID')] if 'ItemID' in cols else '?'}) ---")
        for c, v in zip(cols, row):
            if v is not None and str(v).strip() != '':
                print(f"    {c}: {v}")

        # רשומות פר-סניף — גם שם יש לפעמים תאריכי יצירה
        if 'ItemID' in cols:
            item_id = row[cols.index('ItemID')]
            cur.execute("SELECT * FROM ItemStore WHERE ItemID = %s", (item_id,))
            scols = [d[0] for d in cur.description]
            for srow in cur.fetchall():
                date_vals = [(c, v) for c, v in zip(scols, srow)
                             if v is not None and ('date' in c.lower() or 'time' in c.lower() or 'store' in c.lower())]
                print(f"  --- ItemStore ---")
                for c, v in date_vals:
                    print(f"    {c}: {v}")

            # התנועה הראשונה של הפריט (קנייה/מכירה ראשונה) — אינדיקציה נוספת למועד ההקמה
            try:
                cur.execute("""
                    SELECT MIN(t.TransactionTime) FROM TransactionEntry te
                    JOIN [Transaction] t ON te.TransactionID = t.TransactionID
                    JOIN ItemStore ist ON ist.ItemStoreID = te.ItemStoreID
                    WHERE ist.ItemID = %s
                """, (item_id,))
                first_tx = cur.fetchone()[0]
                print(f"  תנועה ראשונה בקופה: {first_tx}")
            except Exception as e:
                print(f"  (שליפת תנועה ראשונה נכשלה: {e!r})")
    conn.close()
