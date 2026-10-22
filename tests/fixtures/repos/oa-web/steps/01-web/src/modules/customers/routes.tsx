import { Route } from "react-router-dom";
import CustomerPage from "./pages/CustomerPage";

export const customerRoutes = <Route path="/customers/:customerId" element={<CustomerPage />} />;
