import PhotoCard from "@/components/PhotoCard";

export default function PhotoPage({ params }: { params: { id: string } }) {
  return <PhotoCard id={params.id} />;
}
