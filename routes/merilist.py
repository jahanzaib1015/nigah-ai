from flask import Blueprint, jsonify

import db

merilist_bp = Blueprint("merilist", __name__)


@merilist_bp.route("/meri-list", methods=["GET"])
def meri_list():
    return jsonify({"items": db.get_items(), "success": True})


@merilist_bp.route("/meri-list/<int:item_id>", methods=["DELETE"])
def delete_meri_list(item_id):
    if db.delete_item(item_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Item not found"}), 404
