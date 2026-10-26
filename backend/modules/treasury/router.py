# -*- coding: utf-8 -*-
"""قسم الخزينة والمالية — نقاط النهاية (Router مستقل بالكامل).

المسار الأساسي: /api/v1/treasury/...
التكامل مع المبيعات/المشتريات/المحاسبة يتم عبر طبقات الخدمات فقط.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.config import get_current_user, module_deps
from backend.core.database import get_db
from backend.core.schemas import ApiResponse
from backend.modules.treasury import schemas, services

router = APIRouter(
    prefix="/treasury",
    tags=["الخزينة والمالية"],
    dependencies=[Depends(get_current_user)],
)
health = router.get("/health")(module_deps("treasury"))


# ---------- الصناديق والبنوك ----------
@router.post("/accounts", response_model=ApiResponse[schemas.CashAccountOut],
             summary="إنشاء صندوق أو حساب بنكي")
def create_account(data: schemas.CashAccountCreate, db: Session = Depends(get_db)):
    return ApiResponse.ok(schemas.CashAccountOut.model_validate(
        services.create_cash_account(db, data)), message="تم إنشاء الخزينة/الحساب")


@router.get("/accounts", response_model=ApiResponse[list[schemas.CashAccountOut]],
            summary="قائمة الصناديق والحسابات البنكية")
def list_accounts(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.CashAccountOut.model_validate(a)
                           for a in services.list_cash_accounts(db)])


@router.get("/accounts/{account_id}/balance", response_model=ApiResponse,
            summary="الرصيد الدفتري للصندوق/البنك (من الأستاذ العام)")
def account_balance(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(services.models.CashAccount, account_id)
    if not acc:
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الخزينة غير موجودة")
    return ApiResponse.ok({"account": acc.code, "name": acc.name_ar,
                           "book_balance": services.cash_book_balance(db, acc)})


# ---------- الوردية ----------
@router.post("/shifts/open", response_model=ApiResponse[schemas.ShiftOut],
             summary="فتح وردية أمين خزينة")
def open_shift(data: schemas.ShiftOpen, db: Session = Depends(get_db),
               user: dict = Depends(get_current_user)):
    return ApiResponse.ok(schemas.ShiftOut.model_validate(
        services.open_shift(db, data, user)), message="فُتحت الوردية")


@router.post("/shifts/{shift_id}/close", response_model=ApiResponse[schemas.ShiftOut],
             summary="إغلاق الوردية ومطابقة النقدية (Shift Closing)")
def close_shift(shift_id: int, data: schemas.ShiftClose, db: Session = Depends(get_db),
                user: dict = Depends(get_current_user)):
    s = services.close_shift(db, shift_id, data, user)
    msg = "أُغلقت الوردية"
    if s.variance and abs(s.variance) >= 0.01:
        msg += f" — فرق {s.variance} رُحِّل محاسبيًا ({'عجز' if s.variance < 0 else 'تجاوز'})"
    return ApiResponse.ok(schemas.ShiftOut.model_validate(s), message=msg)


@router.get("/shifts", response_model=ApiResponse[list[schemas.ShiftOut]],
            summary="سجل الورديات")
def list_shifts(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.ShiftOut.model_validate(s)
                           for s in services.list_shifts(db)])


# ---------- سندات القبض والصرف ----------
@router.post("/vouchers", response_model=ApiResponse[schemas.VoucherOut],
             summary="تسجيل سند قبض/صرف (نقدية/شيك/بطاقة/تحويل) مع قيده اللحظي")
def create_voucher(data: schemas.VoucherCreate, db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    v = services.create_voucher(db, data, user)
    return ApiResponse.ok(schemas.VoucherOut.model_validate(v),
                          message=f"سجل السند {v.voucher_no} وقُيِّد بالأستاذ العام")


@router.get("/vouchers", response_model=ApiResponse[list[schemas.VoucherOut]],
            summary="سجل سندات القبض والصرف")
def list_vouchers(direction: str | None = Query(None, pattern="^(receipt|payment)$"),
                  pay_method: str | None = Query(
                      None, pattern="^(cash|check|card|bank_transfer)$"),
                  db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.VoucherOut.model_validate(v)
                           for v in services.list_vouchers(db, direction, pay_method)])


@router.post("/vouchers/{voucher_id}/cancel", response_model=ApiResponse[schemas.VoucherOut],
             summary="إلغاء سند بعكس قيده (Audit Trail)")
def cancel_voucher(voucher_id: int, reason: str = Query(min_length=3),
                   db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    v = services.cancel_voucher(db, voucher_id, reason, user)
    return ApiResponse.ok(schemas.VoucherOut.model_validate(v), message="أُلغي السند وعُكس قيده")


# ---------- الشيكات الواردة ----------
@router.post("/checks/incoming", response_model=ApiResponse[schemas.CheckOut],
             summary="تسجيل شيك وارد من عميل (مدين شيكات تحت التحصيل / دائن العميل)")
def register_incoming_check(data: schemas.IncomingCheckRegister,
                            db: Session = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    c = services.register_incoming_check(db, data, user)
    return ApiResponse.ok(schemas.CheckOut.model_validate(c),
                          message=f"سُجل الشيك {c.check_no} بحالة (تحت التحصيل)")


@router.post("/checks/batches", response_model=ApiResponse, summary="إنشاء حافظة تحصيل وإيداعها بالبنك")
def create_batch(data: schemas.BatchCreate, db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    b = services.create_collection_batch(db, data, user)
    return ApiResponse.ok({"batch_no": b.batch_no, "total": float(b.total_amount),
                           "checks": b.checks_count}, message="أُودعت الحافظة بالبنك")


@router.post("/checks/batches/{batch_id}/settle", response_model=ApiResponse,
             summary="تسوية الحافظة عند إشعار البنك (تحصيل + مرتدات)")
def settle_batch(batch_id: int, data: schemas.BatchSettle, db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    b = services.settle_collection_batch(db, batch_id, data, user)
    return ApiResponse.ok({"batch_no": b.batch_no, "status": b.status},
                          message="سوّيت الحافظة ورحّلت قيودها")


# ---------- الشيكات الصادرة ----------
@router.post("/checks/payment-runs", response_model=ApiResponse,
             summary="دفتر صرف شيكات للموردين (مدين المورد / دائن أوراق دفع)")
def create_payment_run(data: schemas.PaymentRunCreate, db: Session = Depends(get_db),
                       user: dict = Depends(get_current_user)):
    r = services.create_payment_run(db, data, user)
    return ApiResponse.ok({"run_no": r.run_no, "total": float(r.total_amount),
                           "checks": r.checks_count}, message="أُصدرت الشيكات بقيودها")


@router.post("/checks/{check_id}/action", response_model=ApiResponse[schemas.CheckOut],
             summary="تحويلة حالة شيك (deposit/clear/bounce/deliver/return/cancel) مع القيد")
def check_action(check_id: int, data: schemas.CheckAction, db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    c = services.act_on_check(db, check_id, data, user)
    return ApiResponse.ok(schemas.CheckOut.model_validate(c),
                          message=f"الشيك الآن بحالة {c.status}")


@router.get("/checks", response_model=ApiResponse[list[schemas.CheckOut]],
            summary="سجل الشيكات (واردة/صادرة)")
def list_checks(direction: str | None = Query(None, pattern="^(in|out)$"),
                status_: str | None = Query(None, alias="status"),
                db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.CheckOut.model_validate(c)
                           for c in services.list_checks(db, direction, status_)])


# ---------- تسوية البطاقات ----------
@router.post("/cards/settle", response_model=ApiResponse,
             summary="تسوية عمولات بطاقات الائتمان على الإيداع البنكي")
def settle_cards(bank_account_id: int, fees_rate_pct: float = Query(default=2.0, ge=0, le=10),
                 db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    res = services.settle_card_batches(db, bank_account_id, fees_rate_pct, user)
    return ApiResponse.ok(res, message="رحّلت عمولة البطاقات على البنك")


# ---------- التسويات البنكية ----------
@router.post("/reconciliations", response_model=ApiResponse[schemas.ReconciliationOut],
             summary="إنشاء مربع تسوية بنكية (رصيد دفتري ± تعديلات = رصيد فعلي ± تعديلات)")
def create_recon(data: schemas.ReconciliationCreate, db: Session = Depends(get_db),
                 user: dict = Depends(get_current_user)):
    r = services.create_reconciliation(db, data, user)
    return ApiResponse.ok(schemas.ReconciliationOut.model_validate(r),
                          message="أنشئت التسوية" + (" — متوازنة ✓" if r.is_balanced else " — غير متوازنة ✗"))


@router.post("/reconciliations/{recon_id}/finalize", response_model=ApiResponse[schemas.ReconciliationOut],
             summary="اعتماد التسوية وترحيل بنود الدفاتر الناقصة")
def finalize_recon(recon_id: int, data: schemas.ReconciliationFinalize,
                   db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    r = services.finalize_reconciliation(db, recon_id, data, user)
    return ApiResponse.ok(schemas.ReconciliationOut.model_validate(r),
                          message="اعتُمدت التسوية")


@router.get("/reconciliations", response_model=ApiResponse[list[schemas.ReconciliationOut]],
            summary="سجل التسويات البنكية")
def list_recons(db: Session = Depends(get_db)):
    return ApiResponse.ok([schemas.ReconciliationOut.model_validate(r)
                           for r in services.list_reconciliations(db)])


@router.post("/bank-statement/import", response_model=ApiResponse,
             summary="استيراد سطور كشف حساب بنكي للتسوية الآلية")
def import_statement(bank_account_id: int, lines: list[schemas.StatementLineIn],
                     db: Session = Depends(get_db)):
    n = services.import_statement_lines(db, bank_account_id, lines)
    return ApiResponse.ok({"imported": n}, message="استوردت سطور الكشف")
