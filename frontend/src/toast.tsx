// The toasts of the workspace: a short fixed sentence, a colour, and a close button that has a name. The one place that shows one.

import { notifications } from "@mantine/notifications";
import { IconAlertCircle, IconCheck } from "@tabler/icons-react";
import { W } from "./report/words";

export function toast(message: string, kind: "done" | "problem" = "done"): void {
  notifications.show({
    message,
    color: kind === "done" ? "teal" : "orange",
    icon: kind === "done" ? <IconCheck size={16} /> : <IconAlertCircle size={16} />,
    closeButtonProps: { "aria-label": W.toastClose },
  });
}
