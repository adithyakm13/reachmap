export default function PhotoCard({ id }: { id: string }) {
  return <figure data-photo={id} data-card="1">Photo {id}</figure>;
}
