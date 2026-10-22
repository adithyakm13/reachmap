import { Card } from "@mantine/core";
import { ModelNotesSection } from "../ModelNotes";

/** The model notes of the report (M14): apart from the findings and the areas, never evidence, never counted with them. */
export function ModelNotesTab() {
  return (
    <Card padding="md">
      <ModelNotesSection />
    </Card>
  );
}
