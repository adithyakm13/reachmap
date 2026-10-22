export default function Chart({ series }: { series: number[] }) {
  return <svg data-points={series.length} />;
}
