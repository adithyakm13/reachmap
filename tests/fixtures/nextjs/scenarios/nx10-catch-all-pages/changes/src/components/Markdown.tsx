export default function Markdown({ source }: { source: string }) {
  return <article className="md" dangerouslySetInnerHTML={{ __html: source }} />;
}
