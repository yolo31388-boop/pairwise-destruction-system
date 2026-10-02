"""pairwise-destruction-system - DestructionSystem

Structural destruction with damage thresholds, integrity checks and damage
propagation; debris with applied forces, collision bodies and count limits;
support- and stress-aware structural integrity with post-destruction
recalculation; damage-type aware effects with LOD; and state-checked,
synchronized restore.
"""

from __future__ import annotations

import math


class DestructionSystem:
    """Manages destructible objects, debris, integrity, effects and restore."""

    MAX_DEBRIS = 64
    PROPAGATION_FALLOFF = 0.5
    MIN_PROPAGATION_DAMAGE = 1.0
    LOD_NEAR = 20.0
    LOD_FAR = 60.0

    DAMAGE_EFFECTS = {
        "impact": {
            "particles": "dust_cloud",
            "cracks": "radial_cracks",
            "deformation": "dent",
        },
        "explosion": {
            "particles": "sparks_and_smoke",
            "cracks": "shatter_cracks",
            "deformation": "blast_bulge",
        },
        "fire": {
            "particles": "embers",
            "cracks": "thermal_cracks",
            "deformation": "warp",
        },
    }
    DEFAULT_EFFECT = {
        "particles": "dust_cloud",
        "cracks": "radial_cracks",
        "deformation": "dent",
    }

    def __init__(self):
        self.state = {}
        self.objects = {}
        self.debris = []
        self.sync_log = []
        self._next_id = 0

    # -- object registry --------------------------------------------------

    def register_object(self, obj_id=None, *, health=100.0,
                        destruction_threshold=50.0, integrity=1.0,
                        strength=1.0, neighbors=(), supports=(),
                        supported_by=(), grounded=False,
                        position=(0.0, 0.0, 0.0)):
        if obj_id is None:
            obj_id = "obj_%d" % self._next_id
            self._next_id += 1
        self.objects[obj_id] = {
            "id": obj_id,
            "health": float(health),
            "max_health": float(health),
            "destruction_threshold": float(destruction_threshold),
            "integrity": float(integrity),
            "strength": float(strength),
            "neighbors": list(neighbors),
            "supports": list(supports),
            "supported_by": list(supported_by),
            "grounded": bool(grounded),
            "position": tuple(position),
            "state": "intact",
        }
        return obj_id

    def _resolve(self, obj_id):
        if obj_id is None:
            if self.objects:
                return next(iter(self.objects))
            return self.register_object()
        if obj_id not in self.objects:
            self.register_object(obj_id)
        return obj_id

    # -- destruction ------------------------------------------------------

    def calculate_destruction(self, obj_id=None, damage=0.0,
                              damage_type="impact", _visited=None):
        """Apply damage to an object.

        Destruction requires the damage to reach the object's destruction
        threshold AND the object to be structurally compromised (health or
        integrity depleted). Destruction propagates to neighbouring objects
        with distance falloff.
        """
        obj_id = self._resolve(obj_id)
        obj = self.objects[obj_id]
        if _visited is None:
            _visited = set()
        if obj_id in _visited:
            return {"object": obj_id, "damage": 0.0, "destroyed": False,
                    "propagated": [], "skipped": True}
        _visited.add(obj_id)

        damage = max(0.0, float(damage))
        obj["health"] = max(0.0, obj["health"] - damage)
        integrity_info = self.calculate_integrity(obj_id)

        destroyed = (
            damage >= obj["destruction_threshold"]
            and (obj["health"] <= 0.0 or integrity_info["integrity"] <= 0.0)
        )

        debris = []
        effect = None
        propagated = []
        recalculated = []
        if destroyed and obj["state"] != "destroyed":
            obj["state"] = "destroyed"
            obj["integrity"] = 0.0
            debris = self.spawn_debris(obj_id)
            effect = self.apply_destruction_effect(obj_id,
                                                   damage_type=damage_type)
            recalculated = self._recalculate_support(obj_id)

        propagated_damage = damage * self.PROPAGATION_FALLOFF
        if damage >= obj["destruction_threshold"] \
                and propagated_damage >= self.MIN_PROPAGATION_DAMAGE:
            for neighbor_id in obj["neighbors"]:
                if neighbor_id in self.objects and neighbor_id not in _visited:
                    propagated.append(self.calculate_destruction(
                        neighbor_id, propagated_damage, damage_type, _visited))

        return {
            "object": obj_id,
            "damage": damage,
            "damage_type": damage_type,
            "threshold": obj["destruction_threshold"],
            "health": obj["health"],
            "integrity": obj["integrity"],
            "destroyed": destroyed,
            "debris": debris,
            "effect": effect,
            "propagated": propagated,
            "recalculated": recalculated,
        }

    # -- debris -----------------------------------------------------------

    def spawn_debris(self, obj_id=None, count=16, force=None, origin=None):
        """Spawn debris pieces for an object.

        Each piece gets an applied force (so it moves), a collision body (so
        it does not fall through the floor), and the total piece count is
        capped at MAX_DEBRIS to protect the frame rate.
        """
        obj_id = self._resolve(obj_id)
        obj = self.objects[obj_id]
        count = max(1, min(int(count), self.MAX_DEBRIS))
        if force is None:
            force = (0.0, 6.0, 0.0)
        if origin is None:
            origin = obj["position"]
        fx, fy, fz = (float(component) for component in force)

        spawned = []
        for index in range(count):
            angle = index * 2.399963  # golden-angle spread, deterministic
            spread = 1.0 + (index % 5) * 0.2
            velocity = (
                fx + math.cos(angle) * spread,
                fy + (index % 3),
                fz + math.sin(angle) * spread,
            )
            spawned.append({
                "id": "%s_debris_%d" % (obj_id, index),
                "source": obj_id,
                "position": tuple(origin),
                "velocity": velocity,
                "force": (fx, fy, fz),
                "mass": 1.0,
                "has_collision": True,
            })

        self.debris.extend(spawned)
        if len(self.debris) > self.MAX_DEBRIS:
            del self.debris[: len(self.debris) - self.MAX_DEBRIS]
        return spawned

    # -- structural integrity ----------------------------------------------

    def calculate_integrity(self, obj_id=None):
        """Compute structural integrity for an object.

        Integrity accounts for support relations (unsupported objects fall)
        and stress concentration (weak supporting links fail first).
        """
        obj_id = self._resolve(obj_id)
        obj = self.objects[obj_id]

        supporters = [sid for sid in obj["supported_by"]
                      if sid in self.objects
                      and self.objects[sid]["state"] != "destroyed"]
        supported = obj["grounded"] or bool(supporters)

        load = 1.0 + sum(
            1 for other in self.objects.values()
            if obj_id in other["supported_by"] and other["state"] != "destroyed"
        )
        stress = load / max(obj["strength"], 1e-6)
        stress_points = [sid for sid in supporters
                         if self.objects[sid]["strength"] < stress]

        health_ratio = (obj["health"] / obj["max_health"]
                        if obj["max_health"] > 0 else 0.0)
        integrity = health_ratio
        if not supported:
            integrity = 0.0
        elif stress > obj["strength"] * 2.0:
            integrity = min(integrity, 0.5)
        if stress_points:
            integrity = min(integrity, 0.25)

        obj["integrity"] = integrity
        falls = not supported and obj["state"] != "destroyed"
        if falls:
            obj["state"] = "falling"

        return {
            "object": obj_id,
            "integrity": integrity,
            "supported": supported,
            "stress": stress,
            "stress_points": stress_points,
            "falls": falls,
        }

    def _recalculate_support(self, destroyed_id):
        """Re-evaluate integrity of everything the destroyed object held up."""
        recalculated = []
        for other in self.objects.values():
            if destroyed_id in other["supported_by"]:
                info = self.calculate_integrity(other["id"])
                recalculated.append({
                    "object": other["id"],
                    "integrity": info["integrity"],
                    "falls": info["falls"],
                })
        return recalculated

    # -- effects ------------------------------------------------------------

    def apply_destruction_effect(self, obj_id=None, damage_type="impact",
                                 distance=0.0):
        """Build the destruction effect for an object.

        Effects include cracks and deformation (not just particles), vary
        with the damage type, and are scaled down by LOD as distance grows.
        """
        obj_id = self._resolve(obj_id)
        effect = dict(self.DAMAGE_EFFECTS.get(damage_type, self.DEFAULT_EFFECT))

        distance = max(0.0, float(distance))
        if distance <= self.LOD_NEAR:
            lod, lod_label = 0, "high"
        elif distance <= self.LOD_FAR:
            lod, lod_label = 1, "medium"
        else:
            lod, lod_label = 2, "low"

        effect.update({
            "object": obj_id,
            "damage_type": damage_type,
            "lod": lod,
            "lod_label": lod_label,
            "distance": distance,
        })
        return effect

    # -- restore ------------------------------------------------------------

    def restore_object(self, obj_id=None, client_state=None):
        """Restore a damaged object.

        Clears any debris the object left behind, refuses to restore objects
        that were fully destroyed, and syncs the restored state to the
        client so client and server stay consistent.
        """
        obj_id = self._resolve(obj_id)
        obj = self.objects[obj_id]

        if obj["state"] == "destroyed":
            return {
                "object": obj_id,
                "restored": False,
                "reason": "fully_destroyed",
                "debris_cleared": 0,
                "synced": False,
            }

        before = len(self.debris)
        self.debris = [d for d in self.debris if d["source"] != obj_id]
        debris_cleared = before - len(self.debris)

        obj["health"] = obj["max_health"]
        obj["integrity"] = 1.0
        obj["state"] = "intact"

        snapshot = {
            "object": obj_id,
            "state": obj["state"],
            "health": obj["health"],
            "integrity": obj["integrity"],
        }
        self.sync_log.append(snapshot)
        if isinstance(client_state, dict):
            client_state[obj_id] = dict(snapshot)

        return {
            "object": obj_id,
            "restored": True,
            "debris_cleared": debris_cleared,
            "synced": True,
            "state": snapshot,
        }
