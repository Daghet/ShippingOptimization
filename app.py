from flask import Flask, render_template, request
import pulp

app = Flask(__name__)

# Meaningful sample data with cities and shipping rates
sample_data = {
    "order_quantities": {"Laptop": 3, "Mouse": 2, "Keyboard": 1},
    "destination_city": "New York",
    "store_inventories": {
        "North Warehouse": {"Laptop": 2, "Mouse": 1, "Keyboard": 0},
        "South Store": {"Laptop": 1, "Mouse": 2, "Keyboard": 1},
        "City Outlet": {"Laptop": 0, "Mouse": 0, "Keyboard": 0},
    },
    "fixed_shipping_costs": {
        "North Warehouse": 10,
        "South Store": 15,
        "City Outlet": 0,
    },
    "origin_cities": {
        "North Warehouse": "Boston",
        "South Store": "Philadelphia",
        "City Outlet": "Newark",
    },
    "shipping_rates_per_mile": {
        "North Warehouse": 0.5,  # $0.5 per mile
        "South Store": 0.7,      # $0.7 per mile
        "City Outlet": 0.6,      # $0.6 per mile
    }
}

# Mock distance function (replace with API call in production)
def get_distance(origin, destination):
    # Simulated distances in miles (for demo purposes)
    distance_table = {
        ("Boston", "New York"): 215,
        ("Philadelphia", "New York"): 95,
        ("Newark", "New York"): 10,
        ("Boston", "Chicago"): 1000,
        ("Philadelphia", "Chicago"): 800,
        ("Newark", "Chicago"): 790,
    }
    # Return distance if in table, else a default value
    return distance_table.get((origin, destination), 500)  # Default 500 miles if unknown

def calculate_optimal_shipping(order_quantities, store_inventories, fixed_shipping_costs, origin_cities, shipping_rates_per_mile, store_list, destination_city):
    ordered_items = list(order_quantities.keys())
    
    # Check if enough inventory exists
    for item in ordered_items:
        total_available = sum(store_inventories.get(store, {}).get(item, 0) for store in store_list)
        if total_available < order_quantities[item]:
            return f"Error: Not enough stock for {item}. Required: {order_quantities[item]}, Available: {total_available}", None
    
    # Set up the optimization problem
    shipping_problem = pulp.LpProblem("Shipping_Cost_Optimization", pulp.LpMinimize)
    
    # Decision variables
    use_store = pulp.LpVariable.dicts("UseStore", store_list, cat='Binary')
    items_to_ship = pulp.LpVariable.dicts("ItemsToShip", [(store, item) for store in store_list for item in ordered_items], lowBound=0, cat='Continuous')

    # Calculate variable costs based on distance and rate
    variable_shipping_costs = {}
    for store in store_list:
        distance = get_distance(origin_cities[store], destination_city)
        rate = shipping_rates_per_mile[store]
        for item in ordered_items:
            variable_shipping_costs[(store, item)] = distance * rate  # Cost = distance * rate per item

    # Objective: Minimize total shipping cost (fixed + variable)
    shipping_problem += (
        pulp.lpSum([fixed_shipping_costs[store] * use_store[store] for store in store_list]) +
        pulp.lpSum([variable_shipping_costs[(store, item)] * items_to_ship[(store, item)] for store in store_list for item in ordered_items]),
        "Total_Shipping_Cost"
    )

    # Constraint: Fulfill the exact order
    for item in ordered_items:
        shipping_problem += (
            pulp.lpSum([items_to_ship[(store, item)] for store in store_list]) == order_quantities[item],
            f"Fulfill_{item}"
        )
    
    # Constraint: Don’t ship more than available inventory
    for store in store_list:
        for item in ordered_items:
            shipping_problem += (
                items_to_ship[(store, item)] <= store_inventories.get(store, {}).get(item, 0) * use_store[store],
                f"Stock_Limit_{store}_{item}"
            )

    # Solve the problem
    shipping_problem.solve(pulp.PULP_CBC_CMD(msg=0))
    
    if pulp.LpStatus[shipping_problem.status] != 'Optimal':
        return f"No optimal solution found. Solver status: {pulp.LpStatus[shipping_problem.status]}", None
    
    total_cost = pulp.value(shipping_problem.objective)
    if total_cost is None:
        return "Error: Total cost could not be computed.", None
    
    shipping_plan = {}
    for store in store_list:
        store_usage = use_store[store].varValue
        if store_usage is not None and store_usage > 0.5:
            shipping_plan[store] = {
                item: items_to_ship[(store, item)].varValue
                for item in ordered_items
                if items_to_ship[(store, item)].varValue is not None and items_to_ship[(store, item)].varValue > 1e-6
            }
    
    return None, {"total_cost": total_cost, "plan": shipping_plan}

@app.route('/', methods=['GET', 'POST'])
def index():
    default_stores = list(sample_data["store_inventories"].keys())
    shipping_result = None
    error_message = None

    if request.method == 'POST':
        try:
            # Parse destination city
            destination_city = request.form.get('destination_city', '')

            # Parse order quantities
            order_quantities = {}
            i = 0
            while True:
                name_key = f'order_item{i}_name'
                qty_key = f'order_item{i}'
                if name_key not in request.form:
                    break
                item = request.form[name_key]
                if qty_key in request.form and request.form[qty_key]:
                    order_quantities[item] = float(request.form[qty_key])
                i += 1

            # Parse store list
            store_list = []
            origin_cities = {}
            shipping_rates_per_mile = {}
            i = 0
            while True:
                name_key = f'store{i}_name'
                if name_key not in request.form:
                    break
                store_name = request.form[name_key]
                if store_name:
                    store_list.append(store_name)
                    origin_cities[store_name] = request.form.get(f'origin_{store_name}', '')
                    shipping_rates_per_mile[store_name] = float(request.form.get(f'rate_{store_name}', 0))
                i += 1

            # Parse store inventories
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

            # Parse fixed shipping costs
            fixed_shipping_costs = {store: float(request.form.get(f'fixed_{store}', 0)) for store in store_list}

            # Compute the optimal shipping plan
            error_message, shipping_result = calculate_optimal_shipping(
                order_quantities, store_inventories, fixed_shipping_costs, origin_cities, shipping_rates_per_mile, store_list, destination_city
            )
        
        except ValueError:
            error_message = "Invalid input: Please enter numeric values."
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"

    return render_template('index.html', stores=default_stores, result=shipping_result, error=error_message, sample_data=sample_data)

if __name__ == '__main__':
    app.run(debug=True)