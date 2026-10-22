"""آزمون‌های اطلاعات مسافران: رمزنگاری، ماسک، دسترسی، ثبت دیدن، پاک‌سازی."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text

from app import dates as jd
from app.db import engine

PASSPORT = "X7744112"


def _trips(client, headers) -> list:
    return client.get("/api/trips", headers=headers).json()["rows"]


def _first_trip(client, headers) -> int:
    rows = _trips(client, headers)
    assert rows, "داده‌ی نمونه باید سفر داشته باشد"
    return rows[0]["id"]


def _add(client, headers, **over) -> dict:
    body = {"full_name": "مسافر آزمون", "kind": "adult", "person_type": 2,
            "passport_no": PASSPORT,
            "passport_expiry": jd.jalali_str(jd.jtoday() + timedelta(days=600))}
    body.update(over)
    r = client.post("/api/passengers", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------ رمزنگاری در فایل داده
def test_passport_is_encrypted_in_the_database_file(client, accountant):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر رمزنگاری")
    with engine.connect() as conn:
        stored = conn.execute(text("select passport_no from passengers where id=:i"),
                              {"i": row["id"]}).scalar()
    assert stored.startswith("enc:v1:"), stored
    assert PASSPORT not in stored
    # و متن ساده در فایل داده پیدا نمی‌شود
    from app import config
    import pathlib
    blob = pathlib.Path(config.DB_PATH).read_bytes()
    assert PASSPORT.encode() not in blob
    # ولی خود برنامه می‌تواند بخواندش
    full = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"]
    mine = [r for r in full if r["id"] == row["id"]][0]
    assert mine["passport_no"] == PASSPORT


def test_national_id_is_encrypted_too(client, accountant):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, person_type=1, passport_no="",
               national_id="0079812345")
    with engine.connect() as conn:
        stored = conn.execute(text("select national_id from passengers where id=:i"),
                              {"i": row["id"]}).scalar()
    assert stored.startswith("enc:v1:") and "0079812345" not in stored


# ------------------------------------------------------------ ماسک و دسترسی
def test_employee_sees_masked_numbers(client, accountant, employee):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر ماسک")
    emp = client.get(f"/api/passengers?trip_id={trip_id}", headers=employee).json()
    mine = [r for r in emp["rows"] if r["id"] == row["id"]][0]
    assert emp["reveal"] is False
    assert "•" in mine["passport_no"] and mine["passport_no"] != PASSPORT
    assert mine["passport_no"].endswith(PASSPORT[-2:])
    acc = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()
    mine2 = [r for r in acc["rows"] if r["id"] == row["id"]][0]
    assert acc["reveal"] is True
    assert mine2["passport_no"] == PASSPORT


def test_employee_cannot_reveal_but_accountant_can_and_it_is_logged(client, accountant, employee):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر دیدن کامل")
    denied = client.post(f"/api/passengers/{row['id']}/reveal", headers=employee, json={})
    assert denied.status_code == 403
    ok = client.post(f"/api/passengers/{row['id']}/reveal", headers=accountant,
                     json={"reason": "تطبیق با گذرنامه"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["row"]["passport_no"] == PASSPORT
    log = client.get("/api/audit?q=passenger.reveal", headers=accountant)
    assert log.status_code == 200
    entries = log.json()["rows"]
    assert any("تطبیق با گذرنامه" in (e.get("detail") or "") for e in entries), entries[:3]


def test_auditor_can_see_full_but_cannot_change(client, accountant, auditor):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر حسابرس")
    seen = client.get(f"/api/passengers?trip_id={trip_id}", headers=auditor).json()
    mine = [r for r in seen["rows"] if r["id"] == row["id"]][0]
    assert mine["passport_no"] == PASSPORT
    edit = client.patch(f"/api/passengers/{row['id']}", headers=auditor,
                        json={"note": "تغییر حسابرس"})
    assert edit.status_code == 403


# ------------------------------------------------------------ درست بودن داده
def test_foreign_passenger_needs_passport(client, accountant):
    trip_id = _first_trip(client, accountant)
    r = client.post("/api/passengers", headers=accountant, json={
        "full_name": "مسافر بدون گذرنامه", "person_type": 2, "trip_id": trip_id})
    assert r.status_code == 400
    assert "گذرنامه" in r.json()["detail"]


def test_national_id_must_be_ten_digits(client, accountant):
    trip_id = _first_trip(client, accountant)
    r = client.post("/api/passengers", headers=accountant, json={
        "full_name": "مسافر کد کوتاه", "person_type": 1, "national_id": "12345",
        "trip_id": trip_id})
    assert r.status_code == 400
    assert "۱۰ رقم" in r.json()["detail"]


def test_passenger_without_owner_is_rejected(client, accountant):
    r = client.post("/api/passengers", headers=accountant,
                    json={"full_name": "بی‌سفر", "person_type": 1, "national_id": "0012345678"})
    assert r.status_code == 400
    assert "سفر یا یک فاکتور" in r.json()["detail"]


def test_update_delete_and_expiry_state(client, accountant):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر ویرایش",
               passport_expiry=jd.jalali_str(jd.jtoday() + timedelta(days=60)))
    listed = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"]
    mine = [r for r in listed if r["id"] == row["id"]][0]
    assert mine["passport_state"] == "soon"          # کمتر از ۶ ماه
    assert "۶ ماه" in mine["passport_state_label"]
    upd = client.patch(f"/api/passengers/{row['id']}", headers=accountant,
                       json={"full_name": "مسافر ویرایش‌شده", "kind": "child",
                             "passport_expiry": jd.jalali_str(jd.jtoday() - timedelta(days=1))})
    assert upd.status_code == 200
    listed2 = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"]
    mine2 = [r for r in listed2 if r["id"] == row["id"]][0]
    assert mine2["name"] == "مسافر ویرایش‌شده" and mine2["kind_label"] == "کودک"
    assert mine2["passport_state"] == "expired"
    assert client.delete(f"/api/passengers/{row['id']}", headers=accountant).status_code == 200
    listed3 = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"]
    assert all(r["id"] != row["id"] for r in listed3)


# ------------------------------------------------------------ پاک‌سازی بعد از سفر
def test_purge_clears_ids_but_keeps_names(client, accountant):
    trip_id = _first_trip(client, accountant)
    row = _add(client, accountant, trip_id=trip_id, full_name="مسافر پاک‌شونده")
    res = client.post(f"/api/trips/{trip_id}/purge-passengers", headers=accountant)
    assert res.status_code == 200, res.text
    assert res.json()["cleared"] >= 1
    after = client.get(f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"]
    mine = [r for r in after if r["id"] == row["id"]][0]
    assert mine["name"] == "مسافر پاک‌شونده"          # نام می‌ماند
    assert mine["passport_no"] == "" and mine["national_id"] == ""
    log = client.get("/api/audit?q=passenger.purge", headers=accountant).json()["rows"]
    assert log, "پاک‌سازی باید در ردّ کار ثبت شود"


def test_employee_cannot_purge(client, accountant, employee):
    trip_id = _first_trip(client, accountant)
    assert client.post(f"/api/trips/{trip_id}/purge-passengers",
                       headers=employee).status_code == 403


# ------------------------------------------------------------ پیوند با فاکتور
def test_link_passengers_to_invoice(client, accountant):
    trip_id = _first_trip(client, accountant)
    party = client.post("/api/parties", headers=accountant,
                        json={"name": "مشتری مسافرها", "kind": "customer"}).json()
    inv = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": trip_id,
        "issue": True, "lines": [{"service_type": "ticket", "unit_price": 50_000_000,
                                  "qty": 1, "taxable": False, "flight_type": 2}]}).json()
    p1 = _add(client, accountant, trip_id=trip_id, full_name="مسافر یکم")
    p2 = _add(client, accountant, trip_id=trip_id, full_name="مسافر دوم")
    link = client.post(f"/api/invoices/{inv['id']}/passengers", headers=accountant,
                       json={"passenger_ids": [p1["id"], p2["id"]]})
    assert link.status_code == 200 and link.json()["count"] == 2, link.text
    linked = client.get(f"/api/passengers?invoice_id={inv['id']}",
                        headers=accountant).json()["rows"]
    assert {r["id"] for r in linked} == {p1["id"], p2["id"]}
    # برداشتن یکی از فاکتور، خود مسافر را پاک نمی‌کند چون به سفر وصل است
    again = client.post(f"/api/invoices/{inv['id']}/passengers", headers=accountant,
                        json={"passenger_ids": [p1["id"]]})
    assert again.json()["count"] == 1
    assert all(r["id"] != p2["id"] for r in client.get(
        f"/api/passengers?invoice_id={inv['id']}", headers=accountant).json()["rows"])
    assert any(r["id"] == p2["id"] for r in client.get(
        f"/api/passengers?trip_id={trip_id}", headers=accountant).json()["rows"])


# ------------------------------------------------------------ پیوند با سامانه‌ی مودیان
def _ticket_moadian(client, accountant, inv_id: int) -> dict:
    client.post("/api/moadian/build", headers=accountant, json={
        "frm": jd.jalali_str(jd.jtoday() - timedelta(days=1)),
        "to": jd.jalali_str(jd.jtoday() + timedelta(days=1))})
    rows = client.get("/api/moadian/queue", headers=accountant).json()["rows"]
    return [r for r in rows if r["invoice_id"] == inv_id][0]


def test_moadian_uses_passenger_passport_for_foreign_flight(client, accountant):
    """آزمون نمونه: شماره‌ی گذرنامه‌ی مسافر در صورتحساب بلیت می‌آید."""
    settings_ok = client.post("/api/moadian/settings", headers=accountant, json={
        "taxid": "A" + "1" * 21, "economic_code": "14009998888",
        "send_method": "motamed", "motamed_name": "شرکت معتمد آزمون"})
    assert settings_ok.status_code == 200
    rows = client.get("/api/moadian/service-map", headers=accountant).json()["rows"]
    for i, r in enumerate(rows, start=1):
        client.patch(f"/api/moadian/service-map/{r['id']}", headers=accountant,
                     json={"stuff_id": f"2717030012{i:03d}", "is_confirmed": True})
    trip_id = _first_trip(client, accountant)
    party = client.post("/api/parties", headers=accountant,
                        json={"name": "مشتری بلیت خارجی", "kind": "customer"}).json()
    inv = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": trip_id,
        "issue": True, "lines": [{"service_type": "ticket", "unit_price": 90_000_000,
                                  "qty": 1, "taxable": False, "flight_type": 2}]}).json()
    row = _ticket_moadian(client, accountant, inv["id"])
    assert any("کد ملی یا شماره‌ی گذرنامه" in p for p in row["problems"]), row["problems"]
    pax = _add(client, accountant, trip_id=trip_id, full_name="مسافر پرواز خارجی")
    client.post(f"/api/invoices/{inv['id']}/passengers", headers=accountant,
                json={"passenger_ids": [pax["id"]]})
    assert client.post(f"/api/moadian/queue/{row['id']}/rebuild",
                       headers=accountant).status_code == 200
    detail = client.get(f"/api/moadian/queue/{row['id']}", headers=accountant).json()
    assert detail["payload"]["header"]["bpn"] == PASSPORT
    assert detail["payload"]["header"]["ft"] == 2
    assert not [p for p in detail["problems"] if "گذرنامه" in p], detail["problems"]
    # مسافر خارجی فقط گذرنامه دارد؛ نباید برنامه کد ملی بخواهد
    assert not detail["problems"], detail["problems"]
    assert detail["status"] == "ready"


def test_moadian_flags_expired_passport(client, accountant):
    trip_id = _first_trip(client, accountant)
    party = client.post("/api/parties", headers=accountant,
                        json={"name": "مشتری گذرنامه منقضی", "kind": "customer"}).json()
    inv = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": trip_id,
        "issue": True, "lines": [{"service_type": "ticket", "unit_price": 90_000_000,
                                  "qty": 1, "taxable": False, "flight_type": 2}]}).json()
    pax = _add(client, accountant, trip_id=trip_id, full_name="مسافر منقضی",
               passport_expiry=jd.jalali_str(jd.jtoday() - timedelta(days=10)))
    client.post(f"/api/invoices/{inv['id']}/passengers", headers=accountant,
                json={"passenger_ids": [pax["id"]]})
    row = _ticket_moadian(client, accountant, inv["id"])
    assert any("منقضی" in p for p in row["problems"]), row["problems"]


def test_passengers_summary_and_excel_masking(client, accountant, employee):
    trip_id = _first_trip(client, accountant)
    _add(client, accountant, trip_id=trip_id, full_name="مسافر خلاصه")
    data = client.get("/api/passengers", headers=accountant).json()
    assert data["summary"]["total"] >= 1
    assert data["summary"]["encryption"]["available"] is True
    import io
    import openpyxl

    def sheet_text(resp) -> str:
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        cells = [str(c.value or "") for row in wb.active.iter_rows() for c in row]
        return " | ".join(cells)

    x_acc = client.get("/api/export/passengers.xlsx", headers=accountant)
    x_emp = client.get("/api/export/passengers.xlsx", headers=employee)
    assert x_acc.status_code == 200 and x_emp.status_code == 200
    assert PASSPORT in sheet_text(x_acc)               # حسابدار فایل کامل می‌گیرد
    assert PASSPORT not in sheet_text(x_emp)           # کارمند فایل ماسک‌شده
    assert "•" in sheet_text(x_emp)


# ------------------------------------------------------------ دسترسی کارمند
def test_employee_can_choose_accounts_but_cannot_see_balances(client, employee, accountant):
    """کارمند برای نوشتن سند به فهرست حساب‌ها نیاز دارد، ولی مانده‌ی حساب‌ها کارِ حسابدار است."""
    emp = client.get("/api/accounts", headers=employee)
    assert emp.status_code == 200, emp.text
    assert emp.json()["limited"] is True
    assert emp.json()["rows"], "فهرست حساب‌ها نباید خالی باشد"
    assert all("balance" not in r for r in emp.json()["rows"])
    acc = client.get("/api/accounts", headers=accountant).json()
    assert acc["limited"] is False
    assert any("balance" in r for r in acc["rows"])


def test_employee_cannot_create_accounts_or_see_salaries(client, employee):
    created = client.post("/api/accounts", headers=employee, json={
        "code": "9999", "name": "حساب غیرمجاز", "nature": "debit"})
    assert created.status_code == 403
    assert client.get("/api/payroll/runs", headers=employee).status_code == 403
