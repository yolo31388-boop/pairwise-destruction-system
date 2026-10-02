"""
pairwise-destruction-system - DestructionSystem

物理破坏系统：破坏阈值与结构完整性校验、破坏传播、碎片物理、
支撑/应力分析、随伤害类型变化的破坏效果（含 LOD）、恢复与状态同步。
"""

from __future__ import annotations

import math


class DestructionSystem:
    """基于破坏阈值与结构完整性的物理破坏系统。"""

    MAX_DEBRIS = 100           # 单次破坏碎片数量上限，防止帧率暴跌
    DEFAULT_HEALTH = 100.0
    DEFAULT_THRESHOLD = 50.0   # 破坏阈值：伤害低于阈值只掉血不碎
    PROPAGATION_FALLOFF = 0.5  # 破坏传播到相邻物体时的伤害衰减系数

    # (距离上限, LOD 等级)，由近到远匹配
    LOD_LEVELS = ((20.0, "high"), (60.0, "medium"), (math.inf, "low"))

    # 不同伤害类型对应不同的破坏效果
    DAMAGE_EFFECTS = {
        "explosion": {"particles": "fireball", "cracks": "radial", "deformation": 0.8},
        "fire": {"particles": "flames", "cracks": "charred", "deformation": 0.3},
        "impact": {"particles": "dust", "cracks": "linear", "deformation": 0.5},
    }
    DEFAULT_EFFECT = {"particles": "dust", "cracks": "linear", "deformation": 0.5}

    def __init__(self):
        self.state = {}
        self.objects = {}
        self.debris = []
        self.client_state = {}
        self.server_state = {}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _make_object(self, obj_id, neighbors=None, supports=None, grounded=True):
        return {
            "id": obj_id,
            "health": self.DEFAULT_HEALTH,
            "threshold": self.DEFAULT_THRESHOLD,
            "integrity": 1.0,
            "neighbors": list(neighbors or []),
            "supports": list(supports or []),
            "grounded": grounded,
            "load": 60.0,
            "strength": 100.0,
            "destroyed": False,
            "collapsed": False,
        }

    def _get_object(self, obj_id):
        if obj_id not in self.objects:
            self._build_default_scene(obj_id)
        return self.objects[obj_id]

    def _build_default_scene(self, obj_id):
        """注册默认场景：目标物体 + 相邻物体 + 由其支撑的悬空楼板。"""
        east, west = f"{obj_id}_east", f"{obj_id}_west"
        floor = f"{obj_id}_floor_above"
        self.objects[obj_id] = self._make_object(obj_id, neighbors=[east, west])
        self.objects[east] = self._make_object(east, neighbors=[obj_id])
        self.objects[west] = self._make_object(west, neighbors=[obj_id])
        self.objects[floor] = self._make_object(floor, supports=[obj_id], grounded=False)

    def _snapshot(self, obj):
        return {
            "id": obj["id"],
            "health": obj["health"],
            "integrity": obj["integrity"],
            "destroyed": obj["destroyed"],
            "collapsed": obj["collapsed"],
        }

    def _sync(self, obj_id):
        """把物体状态同步到服务器与客户端，保证两端一致。"""
        snapshot = self._snapshot(self.objects[obj_id])
        self.server_state[obj_id] = snapshot
        self.client_state[obj_id] = dict(snapshot)

    # ------------------------------------------------------------------
    # 破坏计算
    # ------------------------------------------------------------------
    def calculate_destruction(self, obj_id="wall", damage=100.0,
                              damage_type="explosion", radius=5.0):
        """计算破坏：检查破坏阈值与结构完整性，破坏后生成碎片并向相邻物体传播。"""
        obj = self._get_object(obj_id)
        result = {
            "object": obj_id,
            "damage": damage,
            "destroyed": False,
            "debris": [],
            "effect": None,
            "propagated": [],
            "collapsed": [],
        }
        if obj["destroyed"]:
            result["destroyed"] = True
            return result

        # 1. 伤害先扣血
        obj["health"] = max(0.0, obj["health"] - damage)

        # 2. 破坏判定：伤害需达到破坏阈值，或结构完整性已归零
        integrity = self.calculate_integrity(obj_id)["integrity"]
        if damage < obj["threshold"] and integrity > 0.0:
            result["remaining_health"] = obj["health"]
            return result

        # 3. 破坏：生成碎片与破坏效果，物体不是直接消失
        obj["destroyed"] = True
        obj["integrity"] = 0.0
        result["destroyed"] = True
        result["debris"] = self.spawn_debris(obj_id, count=int(damage // 10) + 8)
        result["effect"] = self.apply_destruction_effect(obj_id, damage_type)

        # 4. 破坏传播：相邻物体受到衰减伤害
        for neighbor_id in obj["neighbors"]:
            neighbor = self.objects.get(neighbor_id)
            if neighbor is None or neighbor["destroyed"]:
                continue
            sub = self.calculate_destruction(
                neighbor_id, damage * self.PROPAGATION_FALLOFF, damage_type, radius)
            result["propagated"].append(
                {"object": neighbor_id, "destroyed": sub["destroyed"]})
            for fallen in sub["collapsed"]:
                if fallen not in result["collapsed"]:
                    result["collapsed"].append(fallen)

        # 5. 破坏后重新计算支撑：失去支撑的悬空物体坍塌
        for other_id, other in self.objects.items():
            if other["destroyed"] or other["collapsed"]:
                continue
            check = self.calculate_integrity(other_id)
            if not check["supported"] and not other["grounded"]:
                other["collapsed"] = True
                result["collapsed"].append(other_id)
                result["debris"] += self.spawn_debris(other_id, count=6)

        self._sync(obj_id)
        return result

    # ------------------------------------------------------------------
    # 碎片物理
    # ------------------------------------------------------------------
    def spawn_debris(self, source_id="wall", count=10, force=(0.0, 8.0, 0.0)):
        """生成碎片：应用力获得初速度、附带碰撞体，并限制数量上限。"""
        count = max(0, min(int(count), self.MAX_DEBRIS))
        spawned = []
        for i in range(count):
            mass = 0.5 + (i % 5) * 0.25
            # 应用力：v = F / m，再加确定性散布，碎片会飞出去而不是原地不动
            spread = ((i % 3) - 1) * 1.5
            velocity = tuple(f / mass + spread for f in force)
            debris = {
                "id": f"{source_id}_debris_{len(self.debris) + len(spawned)}",
                "source": source_id,
                "position": (0.0, 0.5, 0.0),
                "velocity": velocity,
                "force": force,
                "mass": mass,
                "collider": True,   # 有碰撞体，不会穿过地面
            }
            spawned.append(debris)
        self.debris.extend(spawned)
        return spawned

    # ------------------------------------------------------------------
    # 结构完整性
    # ------------------------------------------------------------------
    def calculate_integrity(self, obj_id="wall"):
        """计算结构完整性：检查支撑关系与应力集中。"""
        obj = self._get_object(obj_id)

        # 1. 支撑检查：不在地面且没有存活支撑的物体悬空，完整性归零
        live_supports = [
            s for s in obj["supports"]
            if s in self.objects and not self.objects[s]["destroyed"]
        ]
        if obj["grounded"]:
            supported = True
        elif obj["supports"]:
            supported = bool(live_supports)
        else:
            supported = False

        # 2. 应力集中：载荷超过薄弱环节强度时，薄弱处先断
        stress = obj["load"] / obj["strength"] if obj["strength"] > 0 else math.inf
        weak_point_failure = stress >= 1.0

        # 3. 综合完整性
        integrity = obj["health"] / self.DEFAULT_HEALTH
        if weak_point_failure:
            integrity *= 0.5
        if not supported:
            integrity = 0.0
        obj["integrity"] = integrity

        return {
            "object": obj_id,
            "integrity": integrity,
            "supported": supported,
            "supports": live_supports,
            "stress": stress,
            "weak_point_failure": weak_point_failure,
            "falls": not supported,
        }

    # ------------------------------------------------------------------
    # 破坏效果
    # ------------------------------------------------------------------
    def apply_destruction_effect(self, obj_id="wall", damage_type="explosion",
                                 distance=10.0):
        """应用破坏效果：粒子 + 裂痕 + 变形，随伤害类型变化，按距离做 LOD。"""
        obj = self._get_object(obj_id)
        effect = self.DAMAGE_EFFECTS.get(damage_type, self.DEFAULT_EFFECT)
        lod = next(level for limit, level in self.LOD_LEVELS if distance <= limit)
        result = {
            "object": obj_id,
            "damage_type": damage_type,
            "particles": effect["particles"],
            "cracks": effect["cracks"],
            "deformation": effect["deformation"],
            "lod": lod,
        }
        obj["last_effect"] = result
        return result

    # ------------------------------------------------------------------
    # 破坏恢复
    # ------------------------------------------------------------------
    def restore_object(self, obj_id="wall"):
        """恢复物体：清理残留碎片、检查状态（完全破坏不可恢复）、同步双端。"""
        obj = self._get_object(obj_id)

        # 状态检查：完全破坏的物体不能恢复
        if obj["destroyed"] and obj["health"] <= 0.0 and obj["integrity"] <= 0.0:
            return {"object": obj_id, "restored": False,
                    "reason": "completely_destroyed"}

        # 清理该物体残留在场景里的碎片
        self.debris = [d for d in self.debris if d["source"] != obj_id]

        # 恢复结构状态
        obj["health"] = self.DEFAULT_HEALTH
        obj["integrity"] = 1.0
        obj["destroyed"] = False
        obj["collapsed"] = False

        # 同步客户端与服务器状态
        self._sync(obj_id)
        return {"object": obj_id, "restored": True,
                "state": self.server_state[obj_id]}
