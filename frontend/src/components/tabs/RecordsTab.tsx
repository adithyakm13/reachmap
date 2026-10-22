import { Stack } from "@mantine/core";
import { useReportCtx } from "../../pages/reportContext";
import { RecordsList } from "../Records";

/** What the run could not establish: blocking first, then the others. */
export function RecordsTab() {
  const { view } = useReportCtx();
  return (
    <Stack gap="md">
      <RecordsList records={view.records} completed={view.completed} />
    </Stack>
  );
}
