import Markdown from "@/components/Markdown";

export default function HelpPage({ params }: { params: { topic?: string[] } }) {
  return <Markdown source={(params.topic ?? ["index"]).join("/")} />;
}
