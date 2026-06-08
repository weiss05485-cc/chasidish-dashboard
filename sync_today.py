import json, sys, os
from datetime import datetime
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')

# credentials מ-config.py (מקומי) או environment variables (GitHub Actions)
# תומך במספר מסדים (סניפים): DB_* ראשי, DB2_*/DB3_* אופציונליים — מתאחדים לאותו דשבורד.
try:
    import config as _cfg
except ImportError:
    _cfg = None

def _val(name):
    if _cfg is not None and hasattr(_cfg, name):
        return getattr(_cfg, name)
    return os.environ.get(name)

def _load_databases():
    dbs = []
    for prefix in ('DB', 'DB2', 'DB3', 'DB4'):
        srv = _val(prefix + '_SERVER')
        if not srv:
            continue
        dbs.append({'server': srv, 'name': _val(prefix + '_NAME'),
                    'user': _val(prefix + '_USER'), 'password': _val(prefix + '_PASSWORD')})
    if not dbs:
        raise RuntimeError("לא הוגדרו פרטי חיבור ל-DB")
    return dbs

DATABASES = _load_databases()

def _connect(cfg):
    try:
        import pyodbc
        CONN_STR = (f"DRIVER={{SQL Server}};SERVER={cfg['server']};DATABASE={cfg['name']};"
                    f"UID={cfg['user']};PWD={cfg['password']};Connection Timeout=15;")
        return pyodbc.connect(CONN_STR, timeout=15)
    except Exception:
        import pymssql
        return pymssql.connect(server=cfg['server'], user=cfg['user'],
                               password=cfg['password'], database=cfg['name'],
                               timeout=15, login_timeout=15)


def collect(conn):
    """מריץ את כל השאילתות על מסד אחד ומחזיר את הנתונים הגולמיים."""
    cur = conn.cursor()

    # ── 1. לפי סניף × יום ──
    cur.execute("""
        SELECT
            CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
            st.StoreName,
            ISNULL(SUM(t.Total), 0)               AS TotalSales,
            COUNT(DISTINCT t.TransactionID)       AS Transactions
        FROM [Transaction] t
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1
        WHERE t.Status > -1
          AND t.TransactionType NOT IN (14, 21)
        GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), st.StoreID, st.StoreName
        ORDER BY SaleDate, st.StoreName
    """)
    cols = [d[0] for d in cur.description]
    stores_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in stores_raw:
        r['TotalSales'] = round(float(r['TotalSales']), 2)
        r['Transactions'] = int(r['Transactions'])

    # ── 2. לפי מחלקה × יום ──
    cur.execute("""
        SELECT
            CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
            ISNULL(d.Name, N'ללא מחלקה')          AS Dept,
            SUM(te.Total)                          AS TotalSales
        FROM TransactionEntry te
        JOIN [Transaction] t  ON te.TransactionID = t.TransactionID
        JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1
        LEFT JOIN Department d ON te.DepartmentID = d.DepartmentID
        WHERE t.Status > -1 AND te.Status > -1
          AND te.TransactionEntryType NOT IN (4,10,12,16)
          AND t.TransactionType NOT IN (14, 21)
        GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), d.Name
        ORDER BY SaleDate, TotalSales DESC
    """)
    cols = [d[0] for d in cur.description]
    depts_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in depts_raw:
        r['TotalSales'] = round(float(r['TotalSales']), 2)

    # ── 3. לפי מוכר × יום ──
    cur.execute("""
        SELECT
            CONVERT(VARCHAR(10), t.SaleTime, 23)                           AS SaleDate,
            ISNULL(RTRIM(u.UserFName)+' '+RTRIM(u.UserLName), N'לא ידוע') AS SellerName,
            SUM(t.Total)                                                    AS TotalSales,
            COUNT(DISTINCT t.TransactionID)                                 AS Transactions
        FROM [Transaction] t
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1
        LEFT JOIN Users u ON u.UserId = t.SellerID AND u.Status=1
        WHERE t.Status > -1
          AND t.TransactionType NOT IN (14, 21)
        GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23), u.UserFName, u.UserLName
        ORDER BY SaleDate, TotalSales DESC
    """)
    cols = [d[0] for d in cur.description]
    sellers_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in sellers_raw:
        r['TotalSales'] = round(float(r['TotalSales']), 2)
        r['Transactions'] = int(r['Transactions'])

    # ── 4. סיכום יומי ──
    cur.execute("""
        SELECT
            CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
            ISNULL(SUM(t.Total), 0)               AS TotalSales,
            COUNT(DISTINCT t.TransactionID)       AS Transactions
        FROM [Transaction] t
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1
        WHERE t.Status > -1
          AND t.TransactionType NOT IN (14, 21)
        GROUP BY CONVERT(VARCHAR(10), t.SaleTime, 23)
        ORDER BY SaleDate
    """)
    cols = [d[0] for d in cur.description]
    daily = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d['TotalSales'] = round(float(d['TotalSales']), 2)
        d['Transactions'] = int(d['Transactions'])
        daily.append(d)

    # ── 5. אמצעי תשלום × יום ──
    cur.execute("""
        SELECT SaleDate, PayMethod, SUM(TotalAmount) AS TotalAmount, SUM(Cnt) AS Cnt
        FROM (
            SELECT
                CONVERT(VARCHAR(10), t.SaleTime, 23)                        AS SaleDate,
                ISNULL(tn.TenderNameHe, CAST(te.TenderID AS NVARCHAR(10)))  AS PayMethod,
                te.Amount                                                    AS TotalAmount,
                1                                                            AS Cnt
            FROM TenderEntry te
            JOIN [Transaction] t  ON te.TransactionID = t.TransactionID
            JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1
            LEFT JOIN Tender tn   ON te.TenderID = tn.TenderID
            WHERE t.Status > -1
              AND te.Status > -1

            UNION ALL

            SELECT
                CONVERT(VARCHAR(10), t.SaleTime, 23)  AS SaleDate,
                N'גיפטקארד סימפלי'                    AS PayMethod,
                ABS(tei.Total)                        AS TotalAmount,
                1                                     AS Cnt
            FROM TransactionEntry tei
            JOIN [Transaction] t  ON tei.TransactionID = t.TransactionID
            JOIN Store st         ON t.StoreID = st.StoreID AND st.Status=1
            WHERE t.Status > -1
              AND tei.Status > -1
              AND tei.TransactionEntryType = 18
              AND tei.Total < 0
        ) base
        GROUP BY SaleDate, PayMethod
        ORDER BY SaleDate, TotalAmount DESC
    """)
    cols = [d[0] for d in cur.description]
    payments_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in payments_raw:
        r['TotalAmount'] = round(float(r['TotalAmount']), 2)
        r['Cnt'] = int(r['Cnt'])

    # ── 6. רווחית (אם קיים) ──
    try:
        cur.execute("SELECT Year, Month, Total, Cnt, UpdatedAt FROM RivhitMonthly ORDER BY Year, Month")
        cols = [d[0] for d in cur.description]
        rivhit_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rivhit_raw:
            r['Total'] = round(float(r['Total']), 2)
            r['Cnt']   = int(r['Cnt'])
            if r['UpdatedAt']:
                r['UpdatedAt'] = r['UpdatedAt'].strftime('%d/%m/%Y %H:%M')
    except Exception as e:
        print(f"  רווחית לא זמין: {e}")
        rivhit_raw = []

    # ── 6b. חשבוניות רווחית ──
    try:
        cur.execute("""
            SELECT
                CONVERT(VARCHAR(10), HenDate, 23) AS HenDate,
                HenType, HenTypeName, HenNum, CustName, TtlMam, TtlWith,
                Sgira, SgiraSchum, ISNULL(Commnts, '') AS Commnts,
                CONVERT(VARCHAR(16), UpdatedAt, 120) AS UpdatedAt
            FROM RivhitInvoices
            ORDER BY HenDate DESC
        """)
        cols = [d[0] for d in cur.description]
        rivhit_invoices_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rivhit_invoices_raw:
            r['TtlMam']     = round(float(r['TtlMam'] or 0), 2)
            r['TtlWith']    = round(float(r['TtlWith'] or 0), 2)
            r['SgiraSchum'] = round(float(r['SgiraSchum'] or 0), 2)
            r['Sgira']      = int(r['Sgira'] or 0)
            r['HenNum']     = int(r['HenNum'])
            r['HenType']    = int(r['HenType'])
    except Exception as e:
        print(f"  חשבוניות רווחית לא זמין: {e}")
        rivhit_invoices_raw = []

    # ── 7. חשבוניות ספקים ──
    try:
        cur.execute("""
            SELECT
                CONVERT(VARCHAR(7), sd.DateT, 120)           AS YearMonth,
                ISNULL(sup.Name, N'ללא ספק')                 AS SupplierName,
                CASE sd.Type
                    WHEN 1 THEN N'חשבון' WHEN 2 THEN N'החזרה'
                    WHEN 3 THEN N'חיוב'  WHEN 4 THEN N'זיכוי'
                    ELSE CAST(sd.Type AS NVARCHAR(10))
                END                                           AS DocType,
                COUNT(DISTINCT sd.ID)                         AS DocCount,
                CAST(SUM(ISNULL(sde.ExtPrice, sde.Cost * sde.Qty)) AS DECIMAL(18,2)) AS TotalAmount
            FROM SuppliersDocs sd
            JOIN SuppliersDocsEntry sde ON sde.ID = sd.ID AND sde.Status > 0
            LEFT JOIN Supplier sup      ON sd.SupplierID = sup.SupplierID
            WHERE sd.Status > 0
              AND sd.Type NOT IN (5, 6)
              AND sd.DocStatus IN (7, 8, 9, 10)
              AND sd.DateT >= DATEADD(YEAR, -3, GETDATE())
            GROUP BY CONVERT(VARCHAR(7), sd.DateT, 120), sup.Name, sd.Type
            ORDER BY YearMonth DESC, TotalAmount DESC
        """)
        cols = [d[0] for d in cur.description]
        sup_monthly_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in sup_monthly_raw:
            r['TotalAmount'] = round(float(r['TotalAmount'] or 0), 2)
            r['DocCount']    = int(r['DocCount'])

        cur.execute("""
            SELECT
                CONVERT(VARCHAR(10), sd.DateT, 23)            AS DocDate,
                ISNULL(sup.Name, N'ללא ספק')                  AS SupplierName,
                ISNULL(sd.No, N'')                             AS DocNumber,
                CASE sd.Type
                    WHEN 1 THEN N'חשבון' WHEN 2 THEN N'החזרה'
                    WHEN 3 THEN N'חיוב'  WHEN 4 THEN N'זיכוי'
                    ELSE CAST(sd.Type AS NVARCHAR(10))
                END                                            AS DocType,
                ISNULL(st.StoreName, N'')                      AS StoreName,
                sd.IsPaid                                      AS IsPaid,
                CAST(ISNULL(sd.AmountPay, 0) AS DECIMAL(18,2)) AS AmountPay,
                COUNT(sde.ID)                                  AS LineCount,
                CAST(SUM(sde.Qty) AS DECIMAL(18,1))            AS TotalQty,
                CAST(SUM(ISNULL(sde.ExtPrice, sde.Cost * sde.Qty)) AS DECIMAL(18,2)) AS TotalAmount
            FROM SuppliersDocs sd
            JOIN SuppliersDocsEntry sde ON sde.ID = sd.ID AND sde.Status > 0
            LEFT JOIN Supplier sup      ON sd.SupplierID = sup.SupplierID
            LEFT JOIN Store st          ON sd.StoreID = st.StoreID
            WHERE sd.Status > 0
              AND sd.Type NOT IN (5, 6)
              AND sd.DocStatus IN (7, 8, 9, 10)
              AND sd.DateT >= DATEADD(MONTH, -14, GETDATE())
            GROUP BY sd.DateT, sup.Name, sd.No, sd.Type, st.StoreName, sd.IsPaid, sd.AmountPay
            ORDER BY sd.DateT DESC
        """)
        cols = [d[0] for d in cur.description]
        sup_docs_raw = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in sup_docs_raw:
            r['TotalAmount'] = round(float(r['TotalAmount'] or 0), 2)
            r['TotalQty']    = round(float(r['TotalQty'] or 0), 1)
            r['AmountPay']   = round(float(r['AmountPay'] or 0), 2)
            r['LineCount']   = int(r['LineCount'])
            r['IsPaid']      = int(r['IsPaid'] or 0)
    except Exception as e:
        print(f"  ספקים לא זמין: {e}")
        sup_monthly_raw = []
        sup_docs_raw    = []

    # ── 8. שעות פעילות ──
    cur.execute("""
        SELECT
            DATEPART(HOUR, t.SaleTime)          AS Hour,
            st.StoreName,
            COUNT(DISTINCT t.TransactionID)     AS Transactions,
            CAST(ISNULL(SUM(t.Total),0) AS DECIMAL(18,2)) AS TotalSales
        FROM [Transaction] t
        JOIN Store st ON t.StoreID = st.StoreID AND st.Status=1
        WHERE t.Status > -1
          AND t.TransactionType NOT IN (14, 21)
          AND t.SaleTime >= DATEADD(DAY, -90, GETDATE())
        GROUP BY DATEPART(HOUR, t.SaleTime), st.StoreID, st.StoreName
        ORDER BY Hour, st.StoreName
    """)
    cols = [d[0] for d in cur.description]
    hours_raw = []
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d['Hour']         = int(d['Hour'])
        d['Transactions'] = int(d['Transactions'])
        d['TotalSales']   = round(float(d['TotalSales']), 2)
        hours_raw.append(d)

    return {
        'stores': stores_raw, 'depts': depts_raw, 'sellers': sellers_raw,
        'daily': daily, 'payments': payments_raw, 'hours': hours_raw,
        'rivhit': rivhit_raw, 'rivhit_invoices': rivhit_invoices_raw,
        'sup_monthly': sup_monthly_raw, 'sup_docs': sup_docs_raw,
    }


def _merge_sum(rows, key_fields, sum_fields):
    """מאחד שורות לפי key_fields, מסכם את sum_fields."""
    agg = {}
    order = []
    for r in rows:
        k = tuple(r[f] for f in key_fields)
        if k not in agg:
            agg[k] = dict(r); order.append(k)
        else:
            for sf in sum_fields:
                agg[k][sf] = round(agg[k].get(sf, 0) + r.get(sf, 0), 2)
    return [agg[k] for k in order]


def main():
    parts = []
    for cfg in DATABASES:
        print(f"Connecting to {cfg['name']}@{cfg['server']} ...")
        conn = _connect(cfg)
        try:
            parts.append(collect(conn))
        finally:
            try: conn.close()
            except Exception: pass
        print(f"  ✓ {cfg['name']}: {len(parts[-1]['daily'])} ימים")

    # מיזוג בין הסניפים
    def cat(key): return [r for p in parts for r in p[key]]

    # סניפים: שמות שונים לכל ענף — מאחדים לפי (תאריך, סניף) ליתר ביטחון
    stores   = _merge_sum(cat('stores'),   ['SaleDate', 'StoreName'], ['TotalSales', 'Transactions'])
    # מחלקות / מוכרים / תשלומים / יומי: מסכמים בין הסניפים לכל תאריך
    depts    = _merge_sum(cat('depts'),    ['SaleDate', 'Dept'],       ['TotalSales'])
    sellers  = _merge_sum(cat('sellers'),  ['SaleDate', 'SellerName'], ['TotalSales', 'Transactions'])
    payments = _merge_sum(cat('payments'), ['SaleDate', 'PayMethod'],  ['TotalAmount', 'Cnt'])
    daily    = _merge_sum(cat('daily'),    ['SaleDate'],               ['TotalSales', 'Transactions'])
    daily.sort(key=lambda d: d['SaleDate'])
    # שעות: שם סניף ייחודי לכל ענף — איחוד פשוט
    hours    = cat('hours')
    rivhit          = cat('rivhit')
    rivhit_invoices = cat('rivhit_invoices')
    sup_monthly     = cat('sup_monthly')
    sup_docs        = cat('sup_docs')

    # ── ארגון לפי תאריך ──
    by_date = defaultdict(lambda: {'stores': [], 'depts': [], 'sellers': [], 'payments': []})
    for r in stores:
        dt = r.pop('SaleDate'); by_date[dt]['stores'].append(r)
    for r in depts:
        dt = r.pop('SaleDate'); by_date[dt]['depts'].append(r)
    for r in sellers:
        dt = r.pop('SaleDate'); by_date[dt]['sellers'].append(r)
    for r in payments:
        dt = r.pop('SaleDate'); by_date[dt]['payments'].append(r)

    today_str = datetime.now().strftime('%Y-%m-%d')
    out = {
        'today':            today_str,
        'synced':           datetime.now().strftime('%d/%m/%Y %H:%M'),
        'daily':            daily,
        'by_date':          dict(by_date),
        'rivhit':           rivhit,
        'rivhit_invoices':  rivhit_invoices,
        'sup_monthly':      sup_monthly,
        'sup_docs':         sup_docs,
        'hours_by_store':   hours,
    }
    os.makedirs('docs', exist_ok=True)
    print("שומר today.json...")
    with open('docs/today.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)

    today_total = sum(r['TotalSales'] for r in (by_date.get(today_str, {}).get('stores') or []))
    print(f"✓ today.json — {len(by_date)} ימים | היום: ₪{today_total:,.2f} | "
          f"רווחית: {len(rivhit)} | ספקים: {len(sup_docs)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = str(e)
        if any(x in err for x in ('20009', 'connect', 'Connection timed out',
                                   'unavailable', 'does not exist', 'timeout',
                                   'OperationalError', 'Login failed')):
            import time
            data_file = 'docs/today.json'
            if os.path.exists(data_file):
                age_hours = (time.time() - os.path.getmtime(data_file)) / 3600
                if age_hours > 2:
                    print(f"[ALERT] DB unavailable כבר {age_hours:.1f} שעות!")
                    raise
            print(f"[SKIP] DB unavailable — {err[:120]}")
            print("Sync skipped. No files written. Exiting with code 0.")
            sys.exit(0)
        raise
