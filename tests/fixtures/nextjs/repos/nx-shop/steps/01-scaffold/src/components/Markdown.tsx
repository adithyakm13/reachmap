export default function Markdown({ source }: { source: string }) {
  return <article dangerouslySetInnerHTML={{ __html: source }} />;
}
