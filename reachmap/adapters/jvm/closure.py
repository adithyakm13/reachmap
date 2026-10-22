"""The closure of a change inside one Java or Kotlin file: from the declarations a change touches to every declaration of the file that
spells what changed, to a fixed point, and the names other files import or call those declarations by.

The unit is a member (`Class.method`, a field, a Kotlin property or function), not the top-level declaration the Python and JavaScript
adapters work on: a controller class holds every handler of a service, and a change to one of them must not reach the others.

Rules (docs/briefs/M16-spring.md):

- A unit is reached when it spells a name that was reached: the simple name of a type reached as a whole, or the name of a member
  that was reached. A member name that the repository defines more than `distinct_max` times is not a name to link code by
  (Shape B): units are reached through it only when they use it on its own (`total()`, `service.find()`'s `service`, `this.total`),
  never as the member of something else (`x.total`).
- A type is reached as a whole (all its members, and its simple name) when a line of its header is reached (its annotations, its
  supertypes: they apply to every member) or when a constructor, an initializer block, a static initializer or a method that runs when
  the bean is built or torn down is reached (they run for every use of the type). A constructor is read from its body: the parameters
  are where something is injected.
- What a closure gives other files are names: `T` for a top-level type reached as a whole, `T.m` for one member of it, `f` for a Kotlin
  top-level function or property (and `<File>Kt.f`, the name Java sees it under).

Shadowing is not understood: more names, never fewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from reachmap.adapters.jvm.syntax import Parsed, Unit

__all__ = ["Closure", "close"]

Key = tuple[int, int]  # (index of the version, id of the unit)


@dataclass
class Closure:
    versions: Sequence[Parsed]
    reached: set[Key] = field(default_factory=set)
    whole: set[Key] = field(default_factory=set)
    members: set[str] = field(default_factory=set)  # every member name reached (distinctive or not)
    other: bool = False  # a unit that is not understood was reached: what it does is not told
    import_time: bool = False  # a Kotlin top-level property that runs a call was reached by a name: it runs for every importer of the file

    def units(self, version: int = 0) -> list[Unit]:
        """The reached units of one version, in source order."""
        return [u for u in self.versions[version].units() if (version, id(u)) in self.reached]

    def is_whole(self, version: int, unit: Unit) -> bool:
        return (version, id(unit)) in self.whole

    def locals(self, version: int = 0) -> frozenset[str]:
        return frozenset(u.dotted for u in self.units(version))

    def exports(self, file_class: str = "") -> frozenset[str]:
        """The names other files reach what was reached by: see the module docstring. `file_class` is the name Java sees a Kotlin file's
        top-level functions and properties under (`UtilKt`)."""
        out: set[str] = set()
        wholes: set[str] = set()
        for vi, p in enumerate(self.versions):
            for u in p.units():
                if (vi, id(u)) not in self.reached:
                    continue
                if u.parent is None:
                    if u.is_type:
                        if (vi, id(u)) in self.whole:
                            wholes.add(u.name)
                    elif u.kind != "other":
                        for n in u.names:
                            out.add(n)
                            if file_class:
                                out.add(f"{file_class}.{n}")
                    continue
                top = u.top
                if not top.is_type:
                    continue
                if u.is_type:
                    if (vi, id(u)) in self.whole and u.name != "Companion":
                        out.add(f"{top.name}.{u.name}")
                elif u.kind not in ("other", "constructor", "init") and not u.wide:
                    for n in u.names:
                        out.add(f"{top.name}.{n}")
        out |= wholes
        return frozenset(n for n in out if "." not in n or n.split(".", 1)[0] not in wholes)


def close(versions: Sequence[Parsed], seeds: Iterable[tuple[int, Unit, bool]], distinct: Callable[..., bool], names: Iterable[str] = ()) -> Closure:
    """The closure of the seeds, over every unit of every version. A seed is (version, unit, whole): `whole` says the unit's whole type
    is reached (a line of its header changed). `names` are names that were reached from outside any unit (the name an import statement
    that changed binds): every unit that spells one is reached."""
    c = Closure(versions)
    w_names: set[str] = set()  # simple names of types reached as a whole
    r_distinct: set[str] = set()
    r_generic: set[str] = set()
    r_distinct.update(names)
    r_lazy: set[str] = set()  # names reached with a whole type: counted only when a unit spells one (most are never spelled elsewhere)

    def classify(leaf: str, record: bool) -> None:
        (r_distinct if distinct(leaf, record) else r_generic).add(leaf)
        r_lazy.discard(leaf)

    def add_name(leaf: str, record: bool = True) -> None:
        """A member name was reached: it is followed by name when the repository defines it few enough times (`distinct`; a name that is
        too common is recorded only when the change itself is what reached it, not when a whole type was)."""
        if leaf in c.members:
            return
        c.members.add(leaf)
        if record:
            classify(leaf, True)
        else:
            r_lazy.add(leaf)

    def make_whole(vi: int, t: Unit) -> None:
        key = (vi, id(t))
        if key in c.whole:
            return
        c.whole.add(key)
        c.reached.add(key)
        if t.name != "Companion":
            w_names.add(t.name)
        for child in t.children:
            c.reached.add((vi, id(child)))
            if child.is_type:
                make_whole(vi, child)
            elif child.kind not in ("constructor", "init", "other"):
                for n in child.names:
                    add_name(n, False)

    def reach(vi: int, u: Unit, whole: bool = False, seeded: bool = True) -> None:
        key = (vi, id(u))
        if key in c.reached and not (whole and u.is_type and key not in c.whole):
            return
        c.reached.add(key)
        if u.is_type:
            make_whole(vi, u)
        elif u.kind == "other":
            c.other = True
            if u.parent is not None:
                make_whole(vi, u.parent)
        elif u.wide:
            if u.parent is not None and (seeded or not versions[vi].is_trivial_constructor(u)):  # a constructor that only stores what it is given is no reason
                make_whole(vi, u.parent)
        else:
            for n in u.names:
                add_name(n)

    for vi, u, whole in seeds:
        reach(vi, u, whole)
    grown = True
    while grown:
        grown = False
        for vi, p in enumerate(versions):
            for u in p.units():
                key = (vi, id(u))
                if key in c.reached:
                    continue
                for n in p.reach_spelled(u) & r_lazy:
                    classify(n, False)
                if _hit(p, u, w_names, r_distinct, r_generic):
                    reach(vi, u, seeded=False)
                    if u.kind in ("field", "property") and u.static and p.has_call(u):
                        # initialised when the type (or the file) loads, by a call to something reached: it runs for every use of it
                        if u.parent is None:
                            c.import_time = True
                        else:
                            make_whole(vi, u.parent)
                    grown = True
    return c


def _hit(p: Parsed, u: Unit, w_names: set[str], r_distinct: set[str], r_generic: set[str]) -> bool:
    if not (w_names or r_distinct or r_generic):
        return False
    spelled = p.reach_spelled(u)
    if spelled & w_names or spelled & r_distinct:
        return True
    return bool(r_generic and r_generic & p.reach_free(u))
