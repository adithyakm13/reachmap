import Markdown from "@/components/Markdown";

export default function DocsPage({ params }: { params: { slug: string[] } }) {
  return <Markdown source={params.slug.join("/")} />;
}
