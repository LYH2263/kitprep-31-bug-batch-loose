import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Ingredient
from app.services import prep_service

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _serialize(r: Ingredient) -> dict:
    return {"id": r.id, "code": r.code, "name": r.name, "unit": r.unit,
            "stock_qty": r.stock_qty, "prep_multiple": r.prep_multiple}


@router.get("")
def list_inventory(db: Session = Depends(get_db)):
    return [_serialize(r) for r in db.scalars(select(Ingredient).order_by(Ingredient.id)).all()]


class MultipleIn(BaseModel):
    # null / 空串 = 清掉倍数（没配过，不取整）；其余必须是正数
    prep_multiple: float | str | None = None


@router.put("/{ingredient_id}/multiple")
def save_multiple(ingredient_id: int, body: MultipleIn, db: Session = Depends(get_db)):
    raw = body.prep_multiple
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        multiple: float | None = None
    else:
        if isinstance(raw, str):
            raise HTTPException(400, "倍数必须是正数；0、负数或非数字一律不写入")
        multiple = float(raw)
        if not math.isfinite(multiple) or multiple <= 0:
            # 栏、单、缺料贴全部停在写入前：此刻尚未改任何东西。
            raise HTTPException(400, "倍数必须是正数；0、负数一律不写入")

    try:
        # 与“生成备料单”同一把锁、同一 id 顺序：倍数保存与备料台再生成互斥，
        # 两个并发保存之间也串行，杜绝同一张单被后写覆盖、两边各算一套。
        prep_service.lock_ingredients(db)

        ing = db.get(Ingredient, ingredient_id)
        if ing is None:
            raise HTTPException(404, "原料不存在")

        # 1) 栏先定；结存一行都不碰，保存倍数绝不可能让结存变少。
        ing.prep_multiple = multiple
        # autoflush=False：先把栏的新值 flush 进当前事务，下面的重算才能读到新倍数，
        # 否则会按库里旧倍数重写成另一套数。
        db.flush()

        # 2) 正在用的那些单：每个 open 订单只取最新一张，整张按当前倍数栏重写。
        #    栏 + 所有单在同一事务里：任一单写不进则随事务整体回滚，栏一起退回。
        #    历史单不取、不改；单内结存冻结，只重算占用与缺料。
        ingredients = prep_service.load_ingredient_map(db)
        rewritten = prep_service.rewrite_active_runs(db, ingredient_id, ingredients)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(500, "倍数与备料单未达成一致，已全部退回写入前")

    db.refresh(ing)
    return {**_serialize(ing), "rewritten_runs": rewritten}
