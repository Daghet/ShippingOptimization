from flask import Flask, render_template, request, jsonify
import pulp
import requests
import os
from dotenv import load_dotenv
import base64

load_dotenv()

app = Flask(__name__)

# Sample data with a Canadian store
sample_data = {
    "order_quantities": {"Laptop": 3, "Mouse": 2, "Keyboard": 1},
    "item_weights": {"Laptop": 5.0, "Mouse": 0.2, "Keyboard": 1.0},
    "item_dimensions": {
        "Laptop": {"length": 15, "width": 10, "height": 1},
        "Mouse": {"length": 5, "width": 3, "height": 2},
        "Keyboard": {"length": 18, "width": 6, "height": 1}
    },
    "destination_country": "US",
    "destination_city": "New York",
    "destination_postal": "10001",
    "store_inventories": {
        "North Warehouse": {"Laptop": 2, "Mouse": 1, "Keyboard": 0},
        "South Store": {"Laptop": 1, "Mouse": 2, "Keyboard": 1},
        "City Outlet": {"Laptop": 0, "Mouse": 0, "Keyboard": 0},
        "Toronto Warehouse": {"Laptop": 1, "Mouse": 1, "Keyboard": 1}  # Added Canadian store
    },
    "fixed_shipping_costs": {
        "North Warehouse": 0,
        "South Store": 0,
        "City Outlet": 0,
        "Toronto Warehouse": 0
    },
    "origin_cities": {
        "North Warehouse": "Boston",
        "South Store": "Philadelphia",
        "City Outlet": "Newark",
        "Toronto Warehouse": "Toronto"
    },
    "origin_postals": {
        "North Warehouse": "02108",
        "South Store": "19102",
        "City Outlet": "07102",
        "Toronto Warehouse": "M5V 2T6"  # Canadian postal code
    },
    "origin_countries": {  # Added country mapping
        "North Warehouse": "US",
        "South Store": "US",
        "City Outlet": "US",
        "Toronto Warehouse": "CA"
    }
}

UPS_CLIENT_ID = os.getenv("UPS_CLIENT_ID")
UPS_CLIENT_SECRET = os.getenv("UPS_CLIENT_SECRET")
UPS_TOKEN_URL = "https://onlinetools.ups.com/security/v1/oauth/token"
UPS_API_URL = "https://onlinetools.ups.com/api/rating/v1/shop"

def get_ups_access_token():
    if not all([UPS_CLIENT_ID, UPS_CLIENT_SECRET]):
        raise ValueError("UPS OAuth credentials are missing.")
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"grant_type": "client_credentials"}
    try:
        response = requests.post(UPS_TOKEN_URL, headers=headers, data=data, auth=(UPS_CLIENT_ID, UPS_CLIENT_SECRET))
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        raise Exception(f"Failed to obtain UPS access token: {e}")

def get_ups_shipping_cost(origin_city, origin_postal, origin_country, destination_city, destination_postal, destination_country, items_to_ship, weights, dimensions, access_token):
    total_weight = sum(qty * weights[item] for item, qty in items_to_ship.items())
    max_length = max(dimensions[item]["length"] for item in items_to_ship)
    max_width = max(dimensions[item]["width"] for item in items_to_ship)
    max_height = max(dimensions[item]["height"] for item in items_to_ship)

    payload = {
        "RateRequest": {
            "Request": {"TransactionReference": {"CustomerContext": "Shipping Calc"}},
            "Shipment": {
                "Shipper": {"Address": {"City": origin_city, "PostalCode": origin_postal, "CountryCode": origin_country}},
                "ShipTo": {"Address": {"City": destination_city, "PostalCode": destination_postal, "CountryCode": destination_country}},
                "Package": {
                    "PackagingType": {"Code": "02"},
                    "Dimensions": {"UnitOfMeasurement": {"Code": "IN"}, "Length": str(max_length), "Width": str(max_width), "Height": str(max_height)},
                    "PackageWeight": {"UnitOfMeasurement": {"Code": "LBS"}, "Weight": str(total_weight)}
                },
                "Service": {"Code": "03"}
            }
        }
    }
    
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    try:
        response = requests.post(UPS_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        for shipment in data["RateResponse"]["RatedShipment"]:
            if shipment["Service"]["Code"] == "03":
                cost = float(shipment["TotalCharges"]["MonetaryValue"])
                total_qty = sum(items_to_ship.values())
                return cost, cost / total_qty if total_qty > 0 else cost
        raise ValueError("UPS Ground not found")
    except Exception as e:
        print(f"UPS API error: {e}")
        return 50.0 * sum(items_to_ship.values()), 50.0

def calculate_optimal_shipping(order_quantities, store_inventories, fixed_shipping_costs, origin_cities, origin_postals, origin_countries, store_list, destination_city, destination_postal, item_weights, item_dimensions, destination_country):
    ordered_items = list(order_quantities.keys())
    
    for item in ordered_items:
        total_available = sum(store_inventories.get(store, {}).get(item, 0) for store in store_list)
        if total_available < order_quantities[item]:
            return f"Error: Not enough stock for {item}. Required: {order_quantities[item]}, Available: {total_available}", None
    
    shipping_problem = pulp.LpProblem("Shipping_Cost_Optimization", pulp.LpMinimize)
    use_store = pulp.LpVariable.dicts("UseStore", store_list, cat='Binary')
    items_to_ship = pulp.LpVariable.dicts("ItemsToShip", [(store, item) for store in store_list for item in ordered_items], lowBound=0, cat='Continuous')

    access_token = get_ups_access_token()
    variable_shipping_costs = {}
    store_shipping_totals = {}
    for store in store_list:
        temp_items = {item: order_quantities[item] for item in ordered_items}
        total_cost, cost_per_unit = get_ups_shipping_cost(
            origin_city=origin_cities[store], 
            origin_postal=origin_postals[store],
            origin_country=origin_countries[store],
            destination_city=destination_city, 
            destination_postal=destination_postal,
            destination_country=destination_country,
            items_to_ship=temp_items, 
            weights=item_weights, 
            dimensions=item_dimensions, 
            access_token=access_token
        )
        for item in ordered_items:
            variable_shipping_costs[(store, item)] = cost_per_unit
        store_shipping_totals[store] = total_cost

    shipping_problem += (
        pulp.lpSum([fixed_shipping_costs[store] * use_store[store] for store in store_list]) +
        pulp.lpSum([variable_shipping_costs[(store, item)] * items_to_ship[(store, item)] for store in store_list for item in ordered_items]),
        "Total_Shipping_Cost"
    )

    for item in ordered_items:
        shipping_problem += (
            pulp.lpSum([items_to_ship[(store, item)] for store in store_list]) == order_quantities[item],
            f"Fulfill_{item}"
        )
    for store in store_list:
        for item in ordered_items:
            shipping_problem += (
                items_to_ship[(store, item)] <= store_inventories.get(store, {}).get(item, 0) * use_store[store],
                f"Stock_Limit_{store}_{item}"
            )

    shipping_problem.solve(pulp.PULP_CBC_CMD(msg=0))
    
    if pulp.LpStatus[shipping_problem.status] != 'Optimal':
        return f"No optimal solution found. Solver status: {pulp.LpStatus[shipping_problem.status]}", None
    
    total_cost = pulp.value(shipping_problem.objective)
    if total_cost is None:
        return "Error: Total cost could not be computed.", None
    
    shipping_plan = {}
    cost_breakdown = {}
    for store in store_list:
        store_usage = use_store[store].varValue
        if store_usage is not None and store_usage > 0.5:
            items = {
                item: items_to_ship[(store, item)].varValue
                for item in ordered_items
                if items_to_ship[(store, item)].varValue is not None and items_to_ship[(store, item)].varValue > 1e-6
            }
            if items:
                shipping_plan[store] = items
                shipped_items = {item: qty for item, qty in items.items()}
                shipped_total_cost, _ = get_ups_shipping_cost(
                    origin_city=origin_cities[store], 
                    origin_postal=origin_postals[store],
                    origin_country=origin_countries[store],
                    destination_city=destination_city, 
                    destination_postal=destination_postal,
                    destination_country=destination_country,
                    items_to_ship=shipped_items, 
                    weights=item_weights, 
                    dimensions=item_dimensions, 
                    access_token=access_token
                )
                cost_breakdown[store] = {
                    "shipping_cost": shipped_total_cost,
                    "fixed_cost": fixed_shipping_costs[store]
                }
    
    return None, {"total_cost": total_cost, "plan": shipping_plan, "breakdown": cost_breakdown}

@app.route('/', methods=['GET'])
def index():
    default_stores = list(sample_data["store_inventories"].keys())
    return render_template('index.html', stores=default_stores, sample_data=sample_data)

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        destination_country = request.form.get('destination_country', 'US')
        destination_city = request.form.get('destination_city', '')
        destination_postal = request.form.get('destination_postal', '')

        order_quantities = {}
        item_weights = {}
        item_dimensions = {}
        i = 0
        while True:
            name_key = f'order_item{i}_name'
            qty_key = f'order_item{i}'
            weight_key = f'order_item{i}_weight'
            length_key = f'order_item{i}_length'
            width_key = f'order_item{i}_width'
            height_key = f'order_item{i}_height'
            if name_key not in request.form:
                break
            item = request.form[name_key]
            if qty_key in request.form and request.form[qty_key]:
                order_quantities[item] = float(request.form[qty_key])
                item_weights[item] = float(request.form.get(weight_key, sample_data["item_weights"].get(item, 1.0)))
                item_dimensions[item] = {
                    "length": float(request.form.get(length_key, sample_data["item_dimensions"].get(item, {}).get("length", 10))),
                    "width": float(request.form.get(width_key, sample_data["item_dimensions"].get(item, {}).get("width", 10))),
                    "height": float(request.form.get(height_key, sample_data["item_dimensions"].get(item, {}).get("height", 10)))
                }
            i += 1

        store_list = []
        origin_cities = {}
        origin_postals = {}
        origin_countries = {}
        i = 0
        while True:
            name_key = f'store{i}_name'
            if name_key not in request.form:
                break
            store_name = request.form[name_key]
            if store_name:
                store_list.append(store_name)
                origin_cities[store_name] = request.form.get(f'origin_{store_name}', '')
                origin_postals[store_name] = request.form.get(f'origin_postal_{store_name}', '')
                origin_countries[store_name] = request.form.get(f'origin_country_{store_name}', 'US')
            i += 1

        store_inventories = {}
        for store in store_list:
            store_inventories[store] = {}
            i = 0
            while True:
                qty_key = f'stock_{store}_item{i}'
                name_key = f'stock_{store}_item{i}_name'
                if qty_key not in request.form:
                    break
                if request.form[qty_key] and name_key in request.form:
                    item = request.form[name_key]
                    store_inventories[store][item] = float(request.form[qty_key])
                i += 1

        fixed_shipping_costs = {store: float(request.form.get(f'fixed_{store}', 0)) for store in store_list}

        error_message, shipping_result = calculate_optimal_shipping(
            order_quantities, store_inventories, fixed_shipping_costs, origin_cities, origin_postals, origin_countries, store_list, destination_city, destination_postal, item_weights, item_dimensions, destination_country
        )
        
        if error_message:
            return jsonify({"error": error_message})
        return jsonify(shipping_result)
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True)