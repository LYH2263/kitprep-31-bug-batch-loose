"""备料单落单的唯一口径：载入订单行 / BOM / 倍数栏 → explode → PrepRun 快照。

/prep/run（备料台生成）与原料页保存倍数都走这里，保证两处取整同一口径。
快照一旦落下就是一张单：占用列与缺料贴冻结，之后只允许被“整张按新倍数重写”，
不会被重新打开时现算冒充。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import BomLine, Ingredient, KitchenOrder, OrderLine, PrepRun
from app.services.bom_engine import apply_multiples, explode_and_merge, result_to_dict


def load_ingredient_map(db: Session) -> dict[int, dict]:
    return {
        i.id: {
            "code": i.code,
            "name": i.name,
            "unit": i.unit,
            "stock_qty": i.stock_qty,
            "prep_multiple": i.prep_multiple,
        }
        for i in db.scalars(select(Ingredient).order_by(Ingredient.id)).all()
    }


def lock_ingredients(db: Session, ingredient_id: int | None = None) -> list[Ingredient]:
    """锁原料行以串行化“生成备料单”和“保存倍数”。统一按 id 顺序加锁，避免死锁。

    两条写路径都锁全部原料行（占用列依赖所有原料的倍数；保存时还要整张重写
    在用单），加锁顺序一致，既不会环等，也保证两入口撞在一起时只有先后、
    没有两套取整结果。
    """
    stmt = select(Ingredient).order_by(Ingredient.id).with_for_update()
    if ingredient_id is not None:
        stmt = stmt.where(Ingredient.id == ingredient_id)
    return list(db.scalars(stmt).all())


def build_order_result(db: Session, order: KitchenOrder) -> dict:
    ols = [{"dish_id": l.dish_id, "portions": l.portions}
           for l in db.scalars(select(OrderLine).where(OrderLine.order_id == order.id)).all()]
    bom = [{"dish_id": b.dish_id, "ingredient_id": b.ingredient_id, "qty_per_portion": b.qty_per_portion}
           for b in db.scalars(select(BomLine)).all()]
    result = result_to_dict(explode_and_merge(ols, bom, load_ingredient_map(db)))
    result["order"] = {"id": order.id, "code": order.code, "outlet": order.outlet}
    return result


def create_run(db: Session, order_id: int) -> PrepRun:
    """落一张新单（调用方已持锁）。只在“生成备料单”时调用。"""
    order = db.get(KitchenOrder, order_id)
    if order is None:
        raise LookupError(order_id)
    result = build_order_result(db, order)
    run = PrepRun(order_id=order_id, created_at=datetime.utcnow(),
                  result_json=json.dumps(result, ensure_ascii=False))
    db.add(run)
    db.flush()
    return run


def latest_run(db: Session, order_id: int) -> PrepRun | None:
    return db.scalars(
        select(PrepRun).where(PrepRun.order_id == order_id).order_by(PrepRun.id.desc())
    ).first()


def active_runs_for_ingredient(db: Session, ingredient_id: int) -> list[PrepRun]:
    """各 open 订单“正在用的那张单”（最新一张）中、含该原料行的单子。

    历史单（同订单更早落下的单）与 closed 订单的单一律不出现在返回值里，
    调用方因此没有任何途径把新倍数刷到旧单上——旧单钉死。
    """
    order_ids = list(db.scalars(
        select(KitchenOrder.id)
        .where(KitchenOrder.status == "open")
        .order_by(KitchenOrder.id)
    ).all())
    hits: list[PrepRun] = []
    for oid in order_ids:
        run = latest_run(db, oid)
        if run is None:
            continue
        data = json.loads(run.result_json)
        if any(l.get("ingredient_id") == ingredient_id
               for l in data.get("prep_lines", [])):
            hits.append(run)
    return hits


def rewrite_active_runs(db: Session, hits: list[PrepRun]) -> list[dict]:
    """用当前倍数栏把每张在用单整张重写（行集合不变，只改数）。

    与生成备料单走同一个 apply_multiples / round_up_multiple，两入口同一口径；
    结存冻结在单内，保存倍数绝不从库存页刷新结存。返回重写清单。
    """
    ingredient_map = load_ingredient_map(db)
    written: list[dict] = []
    for run in hits:
        data = json.loads(run.result_json)
        apply_multiples(data, ingredient_map)
        run.result_json = json.dumps(data, ensure_ascii=False)
        written.append({"order_id": run.order_id, "run_id": run.id})
    return written
