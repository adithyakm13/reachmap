// The command palette itself: the one module that holds the library for it, loaded only when the palette is first asked for (see `palette.tsx`).

import { createSpotlight, Spotlight, useSpotlight, type SpotlightActionGroupData } from "@mantine/spotlight";
import { IconSearch } from "@tabler/icons-react";
import { useEffect } from "react";
import { W } from "./report/words";

const [store, actions] = createSpotlight();

export default function PaletteView({ groups, openNow, onOpened }: { groups: SpotlightActionGroupData[]; openNow: boolean; onOpened: (opened: boolean) => void }) {
  const { opened } = useSpotlight(store);
  useEffect(() => {
    if (openNow) {
      actions.open();
    }
  }, [openNow]);
  useEffect(() => {
    onOpened(opened);
  }, [opened, onOpened]);
  return (
    <Spotlight
      actions={groups}
      shortcut="mod + K"
      nothingFound={W.paletteNothing}
      highlightQuery
      limit={40}
      scrollable
      maxHeight={420}
      store={store}
      searchProps={{ leftSection: <IconSearch size={16} />, placeholder: W.palettePlaceholder, "aria-label": W.palettePlaceholder }}
    />
  );
}
