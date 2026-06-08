import hashlib
import json
import os
import re
import sys
from datetime import datetime
from decimal import Decimal

# credentials מ-config.py (מקומי) או environment variables (GitHub Actions)
try:
    from config import DB_SERVER, DB_NAME, DB_USER, DB_PASSWORD
except ImportError:
    DB_SERVER   = os.environ['DB_SERVER']
    DB_NAME     = os.environ['DB_NAME']
    DB_USER     = os.environ['DB_USER']
    DB_PASSWORD = os.environ['DB_PASSWORD']

def _connect():
    """חיבור לDB — pyodbc על Windows, pymssql על Linux (GitHub Actions)"""
    try:
        import pyodbc
        CONN_STR = (f"DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_NAME};"
                    f"UID={DB_USER};PWD={DB_PASSWORD};Connection Timeout=15;")
        return pyodbc.connect(CONN_STR, timeout=15)
    except Exception:
        import pymssql
        return pymssql.connect(server=DB_SERVER, user=DB_USER,
                               password=DB_PASSWORD, database=DB_NAME,
                               timeout=15, login_timeout=15)

BARCODE_RE = re.compile(r'^(\d{2})([A-Za-z]+)(\d{2})([A-Za-z0-9]{2})(.+)$', re.IGNORECASE)

# מחירון עלות ידני — אופציונלי. ריק = משתמשים בעלות מה-DB (AVGCost).
# אפשר למלא בעתיד לפי מחירון הספק של חסידיש: [('שם מוצר', מחיר), ...]
PRICE_LIST = []

def extract_size(barcode):
    m = BARCODE_RE.match(str(barcode or '').strip())
    return m.group(5) if m else ''

def q(cur, sql, params=None):
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def serial(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%d %H:%M")
    return str(obj)

ONHAND_CTE = """
    WITH OnHand AS (
        SELECT ItemID, StoreID, ISNULL(OnHand, 0) AS Qty
        FROM ItemStore
        WHERE Status > -1
    )
"""

def main():
    print("Connecting to SQL Server...")
    conn = _connect()
    cur = conn.cursor()

    print("  Store summary...")
    store_summary = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            st.StoreName,
            st.Code AS StoreCode,
            COUNT(*) AS TotalSKUs,
            SUM(CASE WHEN oh.Qty > 0  THEN 1 ELSE 0 END) AS InStock,
            SUM(CASE WHEN oh.Qty = 0  THEN 1 ELSE 0 END) AS ZeroStock,
            SUM(CASE WHEN ist.ReorderPoint IS NOT NULL AND oh.Qty >= 0
                      AND oh.Qty <= ist.ReorderPoint AND ist.ReorderPoint > 0
                      THEN 1 ELSE 0 END) AS LowStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0)) AS StockValue
        FROM OnHand oh
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
        GROUP BY st.StoreID, st.StoreName, st.Code, st.Sort
        ORDER BY st.Sort
    """)

    print("  Low stock items...")
    low_stock = q(cur, f"""
        {ONHAND_CTE}
        SELECT TOP 300
            im.Name, im.BarcodeNumber, im.ModelNumber,
            st.StoreName,
            CAST(oh.Qty AS DECIMAL(18,1)) AS OnHand,
            CAST(ist.ReorderPoint AS DECIMAL(18,1)) AS ReorderPoint,
            CAST(COALESCE(NULLIF(ist.AVCostWithoutTax, 0), NULLIF(ist.CostWithoutTax, 0), NULLIF(ist.AVGCost / 1.18, 0)) AS DECIMAL(18,2)) AS Price,
            d.Name AS Department
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE ist.ReorderPoint IS NOT NULL AND ist.ReorderPoint > 0
          AND oh.Qty >= 0 AND oh.Qty <= ist.ReorderPoint
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
        ORDER BY (oh.Qty - ist.ReorderPoint) ASC, st.Sort
    """)

    print("  Department breakdown...")
    by_department = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            st.StoreName,
            SUM(CASE WHEN oh.Qty > 0 THEN 1 ELSE 0 END) AS InStock,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty ELSE 0 END) AS DECIMAL(18,1)) AS TotalUnits,
            CAST(SUM(CASE WHEN oh.Qty > 0 THEN oh.Qty * ISNULL(ist.AVGCost, 0) ELSE 0 END)
                 AS DECIMAL(18,0)) AS Value
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
        GROUP BY d.Name, st.StoreName, st.Sort
        HAVING SUM(oh.Qty) > 0
        ORDER BY SUM(oh.Qty * ISNULL(ist.AVGCost, 0)) DESC
    """)

    print("  Search items...")
    flat_items = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            im.ModelNumber,
            st.StoreName,
            CAST(oh.Qty AS DECIMAL(18,1)) AS Qty,
            d.Name AS Department,
            CAST(COALESCE(NULLIF(ist.AVCostWithoutTax, 0), NULLIF(ist.CostWithoutTax, 0), NULLIF(ist.AVGCost / 1.18, 0)) AS DECIMAL(18,2)) AS Price
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        WHERE oh.Qty > 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
    """)

    # Group by barcode → one item with per-store quantities
    item_map = {}
    for row in flat_items:
        bc = str(row['BarcodeNumber'] or '').strip() or row['Name']
        if bc not in item_map:
            item_map[bc] = {
                'n': row['Name'],
                'b': row['BarcodeNumber'] or '',
                'mn': row['ModelNumber'] or '',
                'd': row['Department'] or '',
                'p': 0,
                's': {},
                'q': 0,
            }
        qty   = float(row['Qty'] or 0)
        price = float(row['Price'] or 0)
        item_map[bc]['s'][row['StoreName']] = qty
        item_map[bc]['q'] += qty
        # שמור את המחיר הגבוה ביותר שנמצא (כמה סניפים — עלות שונה; חלקם 0)
        if price > item_map[bc]['p']:
            item_map[bc]['p'] = price

    # --- מילוי מחירים חסרים לפי ברקוד ---
    # ברקוד: YY + עונה + דגם + צבע + מידה  (e.g. 25W0303M40)
    # רמה 1 — מפתח מלא (שנה+עונה+דגם+צבע): אותה שנה, אותו דגם וצבע
    # רמה 2 — מפתח ללא שנה (עונה+דגם+צבע): אותו דגם גם משנים אחרות
    # רמה 1: שנה+עונה+דגם+צבע  (25W0303)
    # רמה 2: עונה+דגם+צבע ללא שנה  (W0303)
    # רמה 3: דגם+צבע בלבד  (0303) — ללא שנה ועונה
    MODEL_FULL_RE    = re.compile(r'^(\d{2}[A-Za-z]+\d{2}[A-Za-z0-9]{2})', re.IGNORECASE)
    MODEL_NOYEAR_RE  = re.compile(r'^\d{2}([A-Za-z]+\d{2}[A-Za-z0-9]{2})', re.IGNORECASE)
    MODEL_NOSEAS_RE  = re.compile(r'^\d{2}[A-Za-z]+(\d{2}[A-Za-z0-9]{2})',  re.IGNORECASE)

    def build_price_map(key_re):
        pm = {}
        for item in item_map.values():
            if item['p'] > 0 and item['b']:
                m = key_re.match(str(item['b']).strip())
                if m and item['p'] > pm.get(m.group(1), 0):
                    pm[m.group(1)] = item['p']
        return pm

    price_full   = build_price_map(MODEL_FULL_RE)
    price_noyear = build_price_map(MODEL_NOYEAR_RE)
    price_noseas = build_price_map(MODEL_NOSEAS_RE)

    filled = 0
    for item in item_map.values():
        if item['p'] == 0 and item['b']:
            bc = str(item['b']).strip()
            m1 = MODEL_FULL_RE.match(bc)
            m2 = MODEL_NOYEAR_RE.match(bc)
            m3 = MODEL_NOSEAS_RE.match(bc)
            p = (price_full.get(m1.group(1))   if m1 else None) or \
                (price_noyear.get(m2.group(1))  if m2 else None) or \
                (price_noseas.get(m3.group(1))  if m3 else None)
            if p:
                item['p'] = p
                filled += 1
    print(f"  Price fill from barcode model: {filled} items filled")

    # --- מחירון ידני גובר על הכל (לפי שם מוצר) ---
    filled_pl = 0
    for item in item_map.values():
        name = item['n']
        for pl_name, pl_price in PRICE_LIST:
            if pl_name in name:
                item['p'] = pl_price
                filled_pl += 1
                break
    print(f"  Price override from price list: {filled_pl} items")

    # --- חישוב שווי מלאי מתוקן בפייתון לפי מחירון ---
    # מחליף את ערכי AVGCost שמגיעים מה-DB (לרוב 0 או נמוך)
    store_value_corrected = {}       # {store_name: float}
    dept_store_value_corrected = {}  # {(dept, store): float}
    for item in item_map.values():
        p = item['p']
        if p <= 0:
            continue
        dept = item['d'] or 'ללא מחלקה'
        for store, qty in item['s'].items():
            if qty > 0:
                store_value_corrected[store] = \
                    store_value_corrected.get(store, 0.0) + qty * p
                ds_key = (dept, store)
                dept_store_value_corrected[ds_key] = \
                    dept_store_value_corrected.get(ds_key, 0.0) + qty * p

    # עדכון store_summary — החלף StockValue מ-SQL בחישוב הנכון
    for s in store_summary:
        sv = store_value_corrected.get(s['StoreName'], 0)
        if sv > 0:
            s['StockValue'] = round(sv)

    # עדכון by_department — החלף Value
    for row in by_department:
        sv = dept_store_value_corrected.get((row['Department'], row['StoreName']), 0)
        if sv > 0:
            row['Value'] = round(sv)

    total_corrected = sum(store_value_corrected.values())
    print(f"  StockValue מתוקן: סה\"כ {total_corrected:,.0f} ₪")

    search_items = list(item_map.values())

    print("  Sales by group/size/month...")
    sales_raw = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            ISNULL(ig.ItemGroupName, N'ללא קבוצה') AS GroupName,
            st.StoreName,
            CONVERT(VARCHAR(7), t.SaleTime, 120) AS YearMonth,
            SUM(te.Qty) AS QtySold
        FROM TransactionEntry te
        JOIN [Transaction] t ON te.TransactionID = t.TransactionID
        JOIN ItemStore ist ON ist.ItemStoreID = te.ItemStoreID
        JOIN ItemMain im ON im.ItemID = ist.ItemID AND im.Status = 1
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        LEFT JOIN (
            SELECT ItemID, ItemGroupID,
                   ROW_NUMBER() OVER (PARTITION BY ItemID ORDER BY CASE WHEN IsMainGroup=1 THEN 0 ELSE 1 END) AS rn
            FROM ItemToGroup WHERE Status = 1
        ) itg ON itg.ItemID = im.ItemID AND itg.rn = 1
        LEFT JOIN ItemGroup ig ON ig.ItemGroupID = itg.ItemGroupID AND ig.Status = 1
        WHERE te.Status > -1 AND t.Status > -1 AND ist.Status > -1
          AND te.TransactionEntryType NOT IN (4, 10, 12, 16)
          AND t.SaleTime >= DATEADD(MONTH, -13, GETDATE())
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
        GROUP BY im.Name, im.BarcodeNumber, d.Name, ig.ItemGroupName,
                 st.StoreName, CONVERT(VARCHAR(7), t.SaleTime, 120)
    """)

    # Process sales: extract size & model code from barcode
    sales_map = {}
    for row in sales_raw:
        bc = str(row['BarcodeNumber'] or '').strip()
        m = BARCODE_RE.match(bc)
        size = m.group(5) if m else ''
        mc   = m.group(4) if m else ''
        key = (row['YearMonth'], row['Department'], row['GroupName'],
               row['Name'].strip(), mc, size, row['StoreName'])
        if key not in sales_map:
            sales_map[key] = 0.0
        sales_map[key] += float(row['QtySold'] or 0)

    sales_items = [
        {'ym': ym, 'dept': dept, 'g': group, 'n': name,
         'mc': mc, 'sz': size, 'st': store, 'q': round(qty)}
        for (ym, dept, group, name, mc, size, store), qty in sales_map.items()
        if qty > 0
    ]

    print("  Reports by group/size...")
    report_raw = q(cur, f"""
        {ONHAND_CTE}
        SELECT
            im.Name,
            im.BarcodeNumber,
            ISNULL(d.Name, N'ללא מחלקה') AS Department,
            ISNULL(ig.ItemGroupName, N'ללא קבוצה') AS GroupName,
            st.StoreName,
            CAST(SUM(oh.Qty) AS DECIMAL(18,1)) AS Qty
        FROM OnHand oh
        JOIN ItemStore ist ON oh.ItemID = ist.ItemID AND oh.StoreID = ist.StoreID
        JOIN ItemMain im ON oh.ItemID = im.ItemID AND im.Status = 1
        JOIN Store st ON oh.StoreID = st.StoreID AND st.Status = 1
        LEFT JOIN Department d ON im.DepartmentID1 = d.DepartmentID
        LEFT JOIN (
            SELECT ItemID, ItemGroupID,
                   ROW_NUMBER() OVER (PARTITION BY ItemID ORDER BY CASE WHEN IsMainGroup=1 THEN 0 ELSE 1 END) AS rn
            FROM ItemToGroup WHERE Status = 1
        ) itg ON itg.ItemID = im.ItemID AND itg.rn = 1
        LEFT JOIN ItemGroup ig ON ig.ItemGroupID = itg.ItemGroupID AND ig.Status = 1
        WHERE oh.Qty >= 0
          AND ISNULL(d.Name, '') NOT IN (N'כללי')
          AND im.Name NOT LIKE N'%כללי%'
          AND im.BarcodeNumber NOT IN ('180','240')
        GROUP BY im.Name, im.BarcodeNumber, d.Name, ig.ItemGroupName, st.StoreName, st.Sort
        ORDER BY st.Sort
    """)

    # Aggregate by (dept, group, name, size) — combine multiple barcodes/years
    report_map = {}
    for row in report_raw:
        bc = str(row['BarcodeNumber'] or '').strip()
        m = BARCODE_RE.match(bc)
        size = m.group(5) if m else ''
        mc   = m.group(4) if m else ''
        key = (row['Department'], row['GroupName'], row['Name'].strip(), mc, size)
        if key not in report_map:
            report_map[key] = {'stores': {}, 'total': 0.0}
        qty = float(row['Qty'] or 0)
        store = row['StoreName']
        report_map[key]['stores'][store] = report_map[key]['stores'].get(store, 0.0) + qty
        report_map[key]['total'] += qty

    report_items = [
        {'dept': dept, 'g': group, 'n': name, 'mc': mc, 'sz': size,
         's': data['stores'], 'q': data['total']}
        for (dept, group, name, mc, size), data in report_map.items()
    ]

    # ── Users (from Arnet) ──
    print("  Users...")
    STORE_CODE_MAP = {1: 'סניף ראשי', 2: 'חסידיש בית שמש', 3: 'חסידיש בני ברק'}
    cur.execute("""
        SELECT
            u.UserNo,
            CAST(u.UserName AS NVARCHAR(200)) AS UserName,
            u.Password,
            LTRIM(RTRIM(ISNULL(CAST(u.UserFName AS NVARCHAR(200)),'') + N' ' + ISNULL(CAST(u.UserLName AS NVARCHAR(200)),''))) AS FullName,
            CAST(u.IsSuperAdmin AS INT) AS IsAdmin,
            u.RoleID,
            (SELECT TOP 1 st2.Code
             FROM UserStore us2
             JOIN Store st2 ON us2.StoreID = st2.StoreID
             WHERE us2.UserID = u.UserId
             ORDER BY st2.Code) AS StoreCode
        FROM Users u
        WHERE u.IsUser = 1
        ORDER BY u.UserNo
    """)
    users_list = []
    for row in cur.fetchall():
        user_no, user_name, password, full_name, is_admin, role_id, store_code = row
        display  = (full_name or '').strip() or str(user_name or user_no or '')
        pwd_str  = str(password or '').strip()
        pwd_hash = hashlib.sha256(pwd_str.encode('utf-8')).hexdigest()
        role     = 'mgmt' if is_admin else 'emp'
        try:
            sc = int(store_code) if store_code is not None else 0
        except (ValueError, TypeError):
            sc = 0
        store = STORE_CODE_MAP.get(sc, '')
        users_list.append({
            'no':      str(user_no  or '').strip(),
            'display': display,
            'user':    str(user_name or '').strip(),
            'hash':    pwd_hash,
            'role':    role,
            'store':   store,
        })

    conn.close()

    os.makedirs("docs", exist_ok=True)

    main_data = {
        "last_updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "store_summary": store_summary,
        "low_stock": low_stock,
        "by_department": by_department,
    }
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(main_data, f, ensure_ascii=False, default=serial)

    with open("docs/search.json", "w", encoding="utf-8") as f:
        json.dump(search_items, f, ensure_ascii=False, default=serial)

    with open("docs/reports.json", "w", encoding="utf-8") as f:
        json.dump(report_items, f, ensure_ascii=False, default=serial)

    with open("docs/sales.json", "w", encoding="utf-8") as f:
        json.dump(sales_items, f, ensure_ascii=False, default=serial)

    with open("docs/users.json", "w", encoding="utf-8") as f:
        json.dump(users_list, f, ensure_ascii=False)

    print(f"Done. {len(search_items)} search | {len(report_items)} report | {len(sales_items)} sales rows")
    for s in store_summary:
        print(f"  {s['StoreName']}: {s['InStock']} in stock / {int(s['TotalUnits'])} units / {int(s['StockValue']):,}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = str(e)
        # שגיאת חיבור לDB — בדוק כמה זמן הנתונים לא עודכנו
        if any(x in err for x in ('20009', 'connect', 'Connection timed out',
                                   'unavailable', 'does not exist', 'timeout',
                                   'OperationalError')):
            import time
            data_file = 'docs/data.json'
            if os.path.exists(data_file):
                age_hours = (time.time() - os.path.getmtime(data_file)) / 3600
                if age_hours > 2:
                    print(f"[ALERT] DB unavailable כבר {age_hours:.1f} שעות! שולח התראה.")
                    raise  # נכשל → GitHub שולח מייל
            print(f"[SKIP] DB unavailable (פחות מ-2 שעות) — {err[:120]}")
            print("Sync skipped. No files written. Exiting with code 0.")
            sys.exit(0)
        raise
