# Sample data
locations = [
    {
        "name": "Store A",
        "inventory": {"laptop": 3, "mouse": 10},
        "distance": 50, # miles to customer
        "rate": 0.5 # $0.50 per mile
    },
    {
        "name": "Store B",
        "inventory": {"laptop": 1, "mouse": 2},
        "distance": 30, # miles to customer
        "rate": 0.6 # $0.60 per mile
    }
]

order = {"laptop": 2, "mouse": 4}
customer_destination = "New York"

# Function to check if store can ship order in full
def check_inventory(order, location):
    # TODO: check if store's inventory has enough of each item
    for item in order:
        needed = order[item]
        if item in location["inventory"]:
            available = location["inventory"][item]
        else:
            available = 0
        if available < needed:
            return False
    return True

# Function to calculate shipping cost
def calculate_shipping_cost(location, order, distance):
    # TODO: check cost of shipping based on distance and rate
    pass

# Main logic
def find_best_shipping_plan(order, location, customer):
    # Step 1: Find closest location
    closest = min(locations, key=lambda x: x["distance"])
    print(f"Closest location: {closest['name']}")

    # Step 2: Check if it can fulfill the order
    if check_inventory(order, closest):
        cost = calculate_shipping_cost(closest, order, closest["distance"])
        return f"Ship from {closest['name']} for ${cost}"
    else:
        print(f"{closest['name']} can't fulfill the order.")
        # TODO: Logic for splitting order across multiple locations
        return "Need to split order (to be implemented)"
    
result = find_best_shipping_plan(order, locations, customer_destination)
print(result)

