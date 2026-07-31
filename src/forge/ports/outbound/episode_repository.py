from typing import Protocol

from forge.domain.memory import Episode


class EpisodeRepository(Protocol):
    """L1 Episode 정본 저장소가 구현해야 하는 outbound 경계.

    최종 수정일: 2026-07-31
    """

    def save(self, episode: Episode) -> Episode:
        """검증된 Episode를 정본 저장소에 저장한다.

        Args:
            episode: Finalize가 만든 불변 L1 경험 단위.

        Returns:
            저장 결과와 projection 상태를 반영한 Episode.

        최종 수정일: 2026-07-31
        """
        ...
