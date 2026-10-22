export default function PhotoCard({ id }: { id: string }) {
  return <figure data-photo={id}>Photo {id}</figure>;
}
