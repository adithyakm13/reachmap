import Nav from "@/components/Nav";

export default function ShopLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="shop shop-wide">
      <Nav />
      <main>{children}</main>
    </div>
  );
}
