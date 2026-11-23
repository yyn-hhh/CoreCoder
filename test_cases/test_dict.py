def get_user_orders(user_ids, db):
    orders = []
    for uid in user_ids:
        user_orders = db.query(f"SELECT * FROM orders WHERE user_id={uid}")
        orders.append(user_orders)
    return orders