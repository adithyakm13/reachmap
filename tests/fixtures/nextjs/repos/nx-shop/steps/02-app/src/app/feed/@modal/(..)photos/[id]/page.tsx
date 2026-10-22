import PhotoCard from "@/components/PhotoCard";

export default function PhotoModal({ params }: { params: { id: string } }) {
  return <PhotoCard id={params.id} />;
}
