import tempfile, os
os.environ["AGENCY_DB_PATH"] = tempfile.mktemp(suffix=".db")
TMP=tempfile.mkdtemp(prefix="report-partial-")
os.environ["AGENCY_UPLOADS"] = TMP
from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
import app.dates as jd
today=jd.jtoday()
# login accountant
def login(u,p):
    r=client.post("/api/auth/login", json={"username":u,"password":p})
    return {"Authorization": f"Bearer {r.json()['token']}"}
h=login("accountant","accountant123")
# --- ساخت سناریو کامل ---
party=client.post("/api/parties", headers=h, json={"name":"مشتری تست گزارش","kind":"customer","phone":"09120000001"}).json()
supplier=client.post("/api/parties", headers=h, json={"name":"هتل تست","kind":"supplier","phone":"09120000002"}).json()
trip=client.post("/api/trips", headers=h, json={"title":"تور آنتالیا","start_date":"1404/07/01","destination":"آنتالیا"}).json()
inv=client.post("/api/invoices", headers=h, json={"party_id":party["id"],"date":"1404/07/10","due_date":"1404/07/20","trip_id":trip["id"],"description":"تور 60M","issue":True,"lines":[{"service_type":"tour","description":"تور","qty":1,"unit_price":"60000000","discount":0,"taxable":False}]}).json()
bill=client.post("/api/bills", headers=h, json={"party_id":supplier["id"],"date":"1404/07/10","trip_id":trip["id"],"net":"40000000","description":"هتل 40M","issue":True}).json()
print(f"فاکتور {inv['number']} total={inv['total']:,}  بیل total={bill['total']:,}")
# پرداخت‌ها
bank=(client.get("/api/reports/treasury", headers=h).json()["rows"][0])
if not bank: bank={"id":1}
bank_id=client.get("/api/accounts", headers=h).json()["rows"]
# پیدا کردن حساب بانکی 110201 یا اولین نقدی
accs=client.get("/api/accounts", headers=h).json()["rows"]
bank_acc=next((a for a in accs if a["code"]=="110201"), accs[0])
# دریافت کامل 60M از مشتری
client.post("/api/settlements", headers=h, json={"direction":"in","party_id":party["id"],"account_id":bank_acc["id"],"amount":"60000000","date":"1404/07/11","description":"وصول کامل","issue":True})
# پرداخت 40M به تامین
client.post("/api/settlements", headers=h, json={"direction":"out","party_id":supplier["id"],"account_id":bank_acc["id"],"amount":"40000000","date":"1404/07/12","description":"پرداخت هتل","issue":True})
print("--- قبل لغو ---")
pl=client.get("/api/reports/profit", headers=h).json()
print(f"سودوزیان: درآمد {pl['revenue']:,} هزینه {pl['cost']:,} سود {pl['profit']:,}")
treas=client.get("/api/reports/treasury", headers=h).json()
print(f"خزانه: {treas['total']:,}")
bal=client.get("/api/reports/balance", headers=h).json()
print(f"تراز: بدهکار {bal['debit']:,} بستانکار {bal['credit']:,} متوازن={bal['balanced']}")
# لغو جزئی 40M
r=client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={"amount":"40000000","reason":"لغو جزئی 40M — انصراف یک مسافر از تور","date":"1404/07/28","penalty_customer":"8000000","supplier_refund":"35000000","penalty_supplier":"5000000","supplier_party_id":supplier["id"]})
print(f"\n--- لغو جزئی ---\n{r.json()}")
print("\n--- بعد لغو ---")
pl=client.get("/api/reports/profit", headers=h).json()
print(f"سودوزیان: درآمد {pl['revenue']:,} هزینه {pl['cost']:,} سود {pl['profit']:,}")
for row in pl.get("revenue_rows",[]): print(f"  درآمد {row['name']}: {row['amount']:,}")
for row in pl.get("cost_rows",[]): print(f"  هزینه {row['name']}: {row['amount']:,}")
treas=client.get("/api/reports/treasury", headers=h).json()
print(f"خزانه: {treas['total']:,} (بانک {treas['rows'][0]['balance']:,})" if treas["rows"] else f"خزانه {treas['total']:,}")
# مانده طرف‌ها
p1=client.get(f"/api/parties/{party['id']}", headers=h).json() if False else None
# از لیست parties مانده بگیر
parties=client.get("/api/parties", headers=h).json()
for p in parties["rows"]:
    if p["id"] in (party["id"], supplier["id"]):
        print(f"مانده {p['name']}: {p['balance']:,}")
bal=client.get("/api/reports/balance", headers=h).json()
print(f"تراز: بدهکار {bal['debit']:,} بستانکار {bal['credit']:,} متوازن={bal['balanced']}")
invs=client.get("/api/invoices?status=all", headers=h).json()
for i in invs["rows"]:
    print(f"فاکتور {i['number']} total={i['total']:,} paid={i['paid']:,} open={i['open']:,} status={i['status']}")
pcs=client.get(f"/api/invoices/{inv['id']}/partial-cancels", headers=h).json()
print(f"تاریخچه لغو جزئی: {pcs}")
led=client.get("/api/entries?limit=20", headers=h).json()
print(f"\nآخرین اسناد ({len(led['rows'])}):")
for e in led["rows"][-6:]:
    print(f"  {e['date']} {e['description'][:50]} ...")
import shutil; shutil.rmtree(TMP, ignore_errors=True)
