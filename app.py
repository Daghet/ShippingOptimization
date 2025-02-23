from flask import Flask, render_template, request, jsonify
import pulp
import requests
import os
from dotenv import load_dotenv
import base64
import logging
import time

load_dotenv()

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

sample_data = {
    "products": {
        "Laptop": {"weight": 5.0, "length": 15, "width": 10, "height": 1},
        "Mouse": {"weight": 0.2, "length": 5, "width": 3, "height": 2},
        "Keyboard": {"weight": 1.0, "length": 18, "width": 6, "height": 1}
    },
    "order_quantities": {"Laptop": 3, "Mouse": 2, "Keyboard": 1},
    "destination_country": "USA",
    "destination_city": "New York",
    "destination_postal": "10001",
    "store_inventories": {
        "North Warehouse": {"Laptop": 2, "Mouse": 1, "Keyboard": 0},
        "South Store": {"Laptop": 1, "Mouse": 2, "Keyboard": 1},
        "City Outlet": {"Laptop": 0, "Mouse": 0, "Keyboard": 0},
        "Toronto Warehouse": {"Laptop": 1, "Mouse": 1, "Keyboard": 1}
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
        "Toronto Warehouse": "M5V 2T6"
    },
    "origin_countries": {
        "North Warehouse": "USA",
        "South Store": "USA",
        "City Outlet": "USA",
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

def get_ups_shipping_cost(origin_city, origin_postal, origin_country, destination_city, destination_postal, destination_country, items_to_ship, products, access_token):
    logger.debug(f"Calling get_ups_shipping_cost with items_to_ship: {items_to_ship}")
    for item in items_to_ship:
        if item not in products:
            logger.warning(f"Item '{item}' not in products, using default dimensions")
            products[item] = {"weight": 1.0, "length": 10, "width": 10, "height": 10}
    total_weight = 0.0
    lengths = []
    widths = []
    heights = []
    for item, quantity in items_to_ship.items():
        total_weight += quantity * products[item]["weight"]
        lengths.append(products[item]["length"])
        widths.append(products[item]["width"])
        heights.append(products[item]["height"])
    max_length = max(lengths)
    max_width = max(widths)
    max_height = max(heights)

    payload = {
        "RateRequest": {
            "Request": {"TransactionReference": {"CustomerContext": "Shipping Calc"}},
            "Shipment": {
                "Shipper": {"Address": {"City": origin_city, "PostalCode": origin_postal, "CountryCode": "US" if origin_country == "USA" else origin_country}},
                "ShipTo": {"Address": {"City": destination_city, "PostalCode": destination_postal, "CountryCode": "US" if destination_country == "USA" else destination_country}},
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
        logger.error(f"UPS API error: {e}")
        return 50.0 * sum(items_to_ship.values()), 50.0

def calculate_optimal_shipping(order_quantities, store_inventories, fixed_shipping_costs, origin_cities, origin_postals, origin_countries, store_list, destination_city, destination_postal, products, destination_country):
    logger.debug(f"Order Quantities: {order_quantities}")
    logger.debug(f"Store Inventories: {store_inventories}")
    logger.debug(f"Products: {products}")
    logger.debug(f"Store List: {store_list}")
    
    ordered_items = list(order_quantities.keys())
    
    for item in ordered_items:
        total_available = sum(store_inventories.get(store, {}).get(item, 0) for store in store_list)
        logger.debug(f"Checking stock for {item}: Required {order_quantities[item]}, Available {total_available}")
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
            products=products,
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
        logger.error(f"No optimal solution found. Solver status: {pulp.LpStatus[shipping_problem.status]}")
        return f"No optimal solution found. Solver status: {pulp.LpStatus[shipping_problem.status]}", None
    
    total_cost = pulp.value(shipping_problem.objective)
    if total_cost is None:
        logger.error("Total cost could not be computed.")
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
                logger.debug(f"Calculating final cost for store {store} with shipped_items: {shipped_items}")
                shipped_total_cost, _ = get_ups_shipping_cost(
                    origin_city=origin_cities[store], 
                    origin_postal=origin_postals[store],
                    origin_country=origin_countries[store],
                    destination_city=destination_city, 
                    destination_postal=destination_postal,
                    destination_country=destination_country,
                    items_to_ship=shipped_items, 
                    products=products,
                    access_token=access_token
                )
                cost_breakdown[store] = {
                    "shipping_cost": shipped_total_cost,
                    "fixed_cost": fixed_shipping_costs[store]
                }
    
    logger.debug(f"Shipping Plan: {shipping_plan}")
    logger.debug(f"Cost Breakdown: {cost_breakdown}")
    return None, {"total_cost": total_cost, "plan": shipping_plan, "breakdown": cost_breakdown}

@app.route('/', methods=['GET'])
def index():
    default_stores = list(sample_data["store_inventories"].keys())
    return render_template('index.html', stores=default_stores, sample_data=sample_data)

@app.route('/calculate', methods=['POST'])
def calculate():
    try:
        start_time = time.time()
        
        destination_country = request.form.get('destination_country', 'USA')
        destination_city = request.form.get('destination_city', '')
        destination_postal = request.form.get('destination_postal', '')

        products = {}
        i = 0
        while True:
            name_key = f'product_name_{i}'
            if name_key not in request.form:
                break
            name = request.form[name_key]
            products[name] = {
                "weight": float(request.form.get(f'product_weight_{i}', 1.0)),
                "length": float(request.form.get(f'product_length_{i}', 10)),
                "width": float(request.form.get(f'product_width_{i}', 10)),
                "height": float(request.form.get(f'product_height_{i}', 10))
            }
            i += 1
        logger.debug(f"Parsed Products: {products}")

        order_quantities = {}
        i = 0
        while True:
            name_key = f'order_item{i}_name'
            qty_key = f'order_item{i}'
            if name_key not in request.form:
                break
            item = request.form[name_key]
            if qty_key in request.form and float(request.form[qty_key]) > 0:
                order_quantities[item] = float(request.form[qty_key])
            i += 1
        logger.debug(f"Parsed Order Quantities: {order_quantities}")

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
                origin_countries[store_name] = request.form.get(f'origin_country_{store_name}', 'USA')
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
        logger.debug(f"Parsed Store Inventories: {store_inventories}")

        fixed_shipping_costs = {store: float(request.form.get(f'fixed_{store}', 0)) for store in store_list}

        api_start_time = time.time()
        error_message, shipping_result = calculate_optimal_shipping(
            order_quantities, store_inventories, fixed_shipping_costs, origin_cities, origin_postals, origin_countries, store_list, destination_city, destination_postal, products, destination_country
        )
        api_end_time = time.time()

        end_time = time.time()
        
        total_time = end_time - start_time
        api_time = api_end_time - api_start_time
        local_time = total_time - api_time

        if error_message:
            logger.error(f"Optimization error: {error_message}")
            return jsonify({"error": error_message})
        shipping_result["timing"] = {
            "total_time": total_time * 1000,  # Convert to ms
            "api_time": api_time * 1000,
            "local_time": local_time * 1000
        }
        return jsonify(shipping_result)
    except Exception as e:
        logger.error(f"Calculate error: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True)