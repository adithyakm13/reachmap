import OrdersScreen from "./screens/OrdersScreen";
import OrderScreen from "./screens/OrderScreen";
import CustomerScreen from "./screens/CustomerScreen";
import StatsScreen from "./screens/StatsScreen";

export const routes = (
  <>
    <Route path="/orders" element={<OrdersScreen />} />
    <Route path="/orders/:orderId" element={<OrderScreen />} />
    <Route path="/customers/:customerId" element={<CustomerScreen />} />
    <Route path="/stats" element={<StatsScreen />} />
  </>
);
