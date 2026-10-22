export default function DashboardLayout({
  children,
  analytics,
}: {
  children: React.ReactNode;
  analytics: React.ReactNode;
}) {
  return (
    <section>
      {children}
      {analytics}
    </section>
  );
}
