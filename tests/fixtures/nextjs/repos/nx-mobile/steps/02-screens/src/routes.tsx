import OrdersScreen from "./screens/OrdersScreen";
import OrderScreen from "./screens/OrderScreen";
import StatusScreen from "./screens/StatusScreen";

export const routes = (
  <>
    <Route path="/orders" element={<OrdersScreen />} />
    <Route path="/orders/:orderId" element={<OrderScreen />} />
    <Route path="/status" element={<StatusScreen />} />
  </>
);
