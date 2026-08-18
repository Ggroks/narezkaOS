"""Стадия render: готовые вертикальные ролики.

BAZA.md §37 и §59. Нарезка идёт с перекодированием, а не копированием потока:
копирование режет по ключевым кадрам с ошибкой в несколько секунд, что для
клипа неприемлемо.

Кадрирование настраивается ([§61](BAZA.md#61)) — см. narezka/core/framing.py.
По умолчанию часть кадра отрезается по бокам, остальное вписывается по ширине
на размытую подложку. Это не статичный центральный кроп из §39: величина
обрезки задаётся пользователем и может быть нулевой, а точка привязки
выбирается. Автоматическое слежение за объектом появится на этапе 4.
"""

from __future__ import annotations

from typing import Any

from narezka.core.cuts import clip_cuts
from narezka.core.tracking import crop_expression
from narezka.core.edl import Edl
from narezka.core.encoders import available as encoder_refusal, video_args
from narezka.core.artifacts import Artifact
from narezka.core.fonts import escape_for_filter, fonts_dir
from narezka.core.framing import (
    CAMERA_LAYOUTS,
    Framing,
    build_layout_filter,
    describe,
    plan_camera,
    plan_frame,
    plan_pip,
    plan_split,
)
from narezka.core.media import find_source, run_tool
from narezka.core.stage import Device, Stage, StageContext, StageSkipped
from narezka.core.clips import clip_inputs, describe_source, load_clips
from narezka.stages.subtitles import INDEX_NAME

SHORTS_INDEX = "index.json"

#: Если ffprobe не отдал размеры, считаем исходник обычным 16:9 — при этом
#: пресеты дают предсказуемый результат, а не падение.
FALLBACK_SIZE = (1920, 1080)


def load_options(ctx: StageContext) -> dict[str, bool]:
    """Что вшивать в ролик. Правки для записи уже учтены в конфиге контекста."""
    out = ctx.config.output
    return {
        "subtitles_enabled": out.subtitles_enabled,
        "loudnorm_enabled": out.loudnorm_enabled,
    }


def load_framing(ctx: StageContext) -> Framing:
    """Настройки кадрирования для этой записи.

    Кадрирование задаётся на запись, а не глобально, потому что подходящее
    зависит от материала: у стрима с вебкой в углу и у записи экрана оно
    разное. Правка накладывается на конфиг при сборке контекста
    (`settings.effective`), здесь остаётся только перевод в рабочий вид.
    """
    return Framing(**ctx.config.output.framing.model_dump())


def source_size(metadata: dict[str, Any]) -> tuple[int, int]:
    video = metadata.get("video") or {}
    width, height = video.get("width"), video.get("height")
    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        return width, height
    return FALLBACK_SIZE


class RenderStage(Stage):
    name = "render"
    group = "shorts"
    #: v7 — нижняя полоса сплита режется по найденной области контента.
    version = 7
    device = Device.ANY
    description = "Вертикальные ролики 9:16 с вшитыми субтитрами"

    def inputs(self, ctx: StageContext) -> list[Artifact]:
        return [
            *clip_inputs(ctx.paths),
            Artifact(ctx.paths.base / "subtitles" / INDEX_NAME),
        ]

    def outputs(self, ctx: StageContext) -> list[Artifact]:
        index = Artifact(ctx.paths.shorts / SHORTS_INDEX)
        artifacts = [index]
        if index.exists():
            try:
                for entry in index.read_json().get("files", []):
                    artifacts.append(Artifact(ctx.paths.shorts / entry["file"]))
            except (ValueError, KeyError, TypeError):
                pass
        return artifacts

    def config_slice(self, ctx: StageContext) -> dict[str, Any]:
        # Ручная правка кадрирования входит в ключ кэша наравне с конфигом:
        # изменил настройку — ролики перерендерятся, не изменил — нет.
        return {
            **ctx.config.output.model_dump(),
            "framing": load_framing(ctx).__dict__,
            **load_options(ctx),
            # Оформление субтитров сюда не входит намеренно: оно приезжает
            # отпечатком указателя субтитров, который лежит во входах.
            # Две записи об одном разъезжаются — здесь стояло имя набора
            # из общего конфига, а рендер брал файлы, собранные по правкам
            # для записи.
        }

    def _prepare(self, ctx: StageContext) -> dict[str, Any]:
        """Всё, что одинаково для всех роликов записи.

        Вынесено, потому что сборка одного ролика идёт тем же путём, что
        и сборка всех: две отдельные дороги к ffmpeg однажды разойдутся,
        и человек будет настраивать по одной картинке, а получать другую.
        """
        out = ctx.config.output
        short = out.short

        clips, clip_source = load_clips(ctx.paths)
        subtitle_index = Artifact(ctx.paths.base / "subtitles" / INDEX_NAME).read_json()
        subtitle_files = {entry["index"]: entry["file"] for entry in subtitle_index.get("files", [])}
        if not subtitle_files:
            raise StageSkipped("нет файлов субтитров")

        source = find_source(ctx.paths.source)
        metadata_artifact = Artifact(ctx.paths.metadata)
        metadata = metadata_artifact.read_json() if metadata_artifact.exists() else {}
        has_video = bool(metadata.get("has_video", True))

        framing = load_framing(ctx)
        options = load_options(ctx)

        # Выбор видеокарты на машине без неё молча превращается в процессор:
        # медленный ролик лучше, чем никакого. Но человек выбирал другое,
        # и узнать об этом он должен здесь, а не по времени сборки.
        refusal = encoder_refusal(out.encoder)
        if refusal is not None:
            ctx.log.warning("сжатие на видеокарте недоступно (%s) — идём процессором", refusal)

        src_w, src_h = source_size(metadata)
        plan = plan_frame(src_w, src_h, short.width, short.height, framing)

        if has_video:
            ctx.log.info(
                "кадрирование «%s»: %s (из %dx%d)",
                framing.preset, describe(plan), src_w, src_h,
            )
        else:
            ctx.log.warning(
                "в источнике нет картинки — ролик собирается на однотонном фоне. "
                "Кадрирование проверяется только на видео"
            )

        subtitles_dir = ctx.paths.base / "subtitles"
        ctx.paths.shorts.mkdir(parents=True, exist_ok=True)

        ctx.log.info(describe_source(clip_source, len(clips)))
        cams = self._facecams(ctx)
        edl = self._edl(ctx)
        if not edl.is_identity:
            ctx.log.info("правки оси времени применяются: отрезков %d", len(edl.spans))

        return {
            "out": out, "short": short, "clips": clips, "clip_source": clip_source,
            "subtitle_files": subtitle_files, "subtitles_dir": subtitles_dir,
            "source": source, "has_video": has_video, "framing": framing,
            "options": options, "plan": plan, "cams": cams, "edl": edl,
            "src_w": src_w, "src_h": src_h,
        }

    def _render_clip(self, ctx: StageContext, prep: dict[str, Any], clip: dict[str, Any]):
        """Собирает один ролик. None — собирать нечего.

        Единственное место, где вызывается ffmpeg для шортса: и полный
        прогон, и пересборка одного ролика идут сюда.
        """
        out, short = prep["out"], prep["short"]
        options, framing, edl = prep["options"], prep["framing"], prep["edl"]

        index = clip["index"]
        name = prep["subtitle_files"].get(index) if options["subtitles_enabled"] else None
        if options["subtitles_enabled"] and name is None:
            return None

        # Клип берётся в исходном времени: ffmpeg перематывает по нему,
        # а вырезки применяет уже внутри отрезка. Границы в клипе после
        # стадии timeline хранятся в выходном времени, поэтому нужен
        # обратный пересчёт — иначе рендерился бы не тот кусок.
        source_start = clip.get("source_start", clip["start"])
        source_end = clip.get("source_end", clip["end"])
        cut_video, cut_audio, duration = clip_cuts(edl, source_start, source_end)
        if duration <= 0:
            ctx.log.warning("клип %d вырезан целиком — пропущен", index)
            return None

        target = Artifact(ctx.paths.shorts / f"{index:02d}.mp4")
        ctx.log.info("рендер %d: %.1f с", index, duration)

        src_w, src_h = prep["src_w"], prep["src_h"]
        split = self._split_for(clip, prep["cams"], framing, src_w, src_h, short)
        track = self._track_for(clip, prep["cams"], framing, src_w, src_h, short)
        pip = self._pip_for(clip, prep["cams"], framing, src_w, src_h)
        camera = self._camera_for(clip, prep["cams"], framing, src_w, src_h, short)

        # Выбранная раскладка бывает неприменима к клипу: вебки в нём нет
        # или она во весь экран. Кадр тогда строится обычным способом —
        # это лучше, чем пустая полоса, — но молчать об этом нельзя:
        # человек выбрал раскладку и вправе знать, что её не вышло.
        if framing.layout in CAMERA_LAYOUTS and split is None and pip is None and camera is None:
            ctx.log.warning(
                "клип %d: раскладка «%s» не применена — вебка не найдена, кадр обычный",
                index, framing.layout,
            )

        follow_crop = camera or (split.cam_crop if split is not None else None)
        follow_x = self._follow_x(clip, prep["cams"], framing, follow_crop, src_w)
        if framing.follow_face and follow_crop is not None and follow_x is None:
            ctx.log.warning(
                "клип %d: слежение за головой не применено — траектория не посчитана. "
                "Разберите запись заново, чтобы она появилась",
                index,
            )

        with target.reserve() as tmp:
            run_tool(
                self._command(
                    split=split,
                    split_x=follow_x if camera is None else None,
                    track=track,
                    pip=pip,
                    camera=camera,
                    camera_x=follow_x if camera is not None else None,
                    source=prep["source"],
                    start=source_start,
                    duration=duration,
                    cut_video=cut_video,
                    cut_audio=cut_audio,
                    subtitle_name=name,
                    loudnorm=options["loudnorm_enabled"],
                    output=tmp,
                    has_video=prep["has_video"],
                    framing=framing,
                    plan=prep["plan"],
                    width=short.width,
                    height=short.height,
                    crf=out.crf,
                    fps=out.fps,
                    encoder=out.encoder,
                    pix_fmt=out.pix_fmt,
                    faststart=out.faststart,
                    lufs=out.loudness_target_lufs,
                ),
                # Минута ролика на CPU — это единицы минут кодирования.
                timeout=1800,
                cwd=prep["subtitles_dir"],
            )

        return {
            "index": index,
            "file": target.path.name,
            "start": clip["start"],
            "end": clip["end"],
            "duration": round(duration, 2),
            "size_bytes": target.path.stat().st_size,
            # Оценка и объяснение доезжают до интерфейса вместе
            # с роликом — иначе непонятно, почему он здесь.
            "interest_score": clip.get("interest_score"),
            "rank": clip.get("rank"),
            "explanation": clip.get("explanation"),
        }

    def run(self, ctx: StageContext) -> None:
        prep = self._prepare(ctx)
        clips, short = prep["clips"], prep["short"]
        framing, plan = prep["framing"], prep["plan"]
        has_video, clip_source = prep["has_video"], prep["clip_source"]

        rendered: list[dict[str, Any]] = []
        for clip in clips:
            ctx.progress(len(rendered) + 1, len(clips), "рендер")
            entry = self._render_clip(ctx, prep, clip)
            if entry is not None:
                rendered.append(entry)

        if not rendered:
            raise StageSkipped("ни один кандидат не отрендерен")

        Artifact(ctx.paths.shorts / SHORTS_INDEX).write_json(
            {
                "width": short.width,
                "height": short.height,
                "framing": {
                    **framing.__dict__,
                    "content_share": round(plan.content_share, 4),
                    "lost_share": round(plan.lost_share, 4),
                    "full_bleed": plan.full_bleed,
                    "summary": describe(plan),
                }
                if has_video
                else None,
                "background": self._background_kind(framing, plan, has_video),
                "source": clip_source,
                "files": rendered,
            }
        )
        total_mb = sum(f["size_bytes"] for f in rendered) / 1024**2
        ctx.log.info("готово роликов %d, суммарно %.1f МБ", len(rendered), total_mb)

    @staticmethod
    def _facecams(ctx: StageContext) -> dict[str, Any]:
        artifact = Artifact(ctx.paths.analysis / "facecam.json")
        if not artifact.exists():
            return {}
        try:
            return artifact.read_json().get("clips", {})
        except ValueError:
            return {}

    @staticmethod
    def _cam_entry(clip, cams):
        """Найденная вебка клипа. None — её нет или она во весь экран."""
        entry = (cams or {}).get(str(clip["index"])) or (cams or {}).get(clip["index"])
        if not isinstance(entry, dict) or entry.get("full_frame"):
            return None
        if not all(key in entry for key in ("x", "y", "width", "height")):
            return None
        return entry

    @staticmethod
    def _rect(entry, key: str):
        """Прямоугольник из записи facecam — лицо или область содержимого."""
        found = (entry or {}).get(key) or {}
        if not found:
            return None
        try:
            return (found["x"], found["y"], found["width"], found["height"])
        except KeyError:
            return None

    @classmethod
    def _pip_for(cls, clip, cams, framing, src_w: int, src_h: int):
        """Вырез под врезку — если раскладка выбрана и вебка найдена.

        Камера во весь экран врезки не требует: врезать её саму в себя
        бессмысленно.
        """
        if framing.layout != "pip":
            return None
        entry = cls._cam_entry(clip, cams)
        if entry is None:
            return None
        return plan_pip(
            src_w, src_h,
            (entry["x"], entry["y"], entry["width"], entry["height"]),
            face=cls._rect(entry, "face"),
            zoom=framing.face_zoom,
            vertical=framing.face_vertical,
        )

    @classmethod
    def _camera_for(cls, clip, cams, framing, src_w: int, src_h: int, short):
        """Вырез под раскладку «только вебка»."""
        if framing.layout != "camera":
            return None
        entry = cls._cam_entry(clip, cams)
        if entry is None:
            return None
        return plan_camera(
            src_w, src_h, short.width, short.height,
            (entry["x"], entry["y"], entry["width"], entry["height"]),
            face=cls._rect(entry, "face"),
            zoom=framing.face_zoom,
            vertical=framing.face_vertical,
        )

    @classmethod
    def _follow_x(cls, clip, cams, framing, crop, src_w: int):
        """Выражение для левой границы выреза, ведомой за головой.

        None — слежение не просили или траектория не посчитана. Второе
        не ошибка: траектория считается стадией facecam только тогда, когда
        раскладка её просит, а на старых записях её может не быть.
        """
        if not framing.follow_face or crop is None:
            return None
        entry = cls._cam_entry(clip, cams) or {}
        points = (entry.get("track") or {}).get("points") if isinstance(entry.get("track"), dict) else None
        if not points:
            return None
        return crop_expression([(float(t), float(x)) for t, x in points], crop[2], src_w)

    @staticmethod
    def _track_for(clip, cams, framing, src_w: int, src_h: int, short):
        """Кроп со слежением — если раскладка выбрана и траектория посчитана."""
        if framing.layout != "track":
            return None
        entry = (cams or {}).get(str(clip["index"])) or (cams or {}).get(clip["index"])
        points = (entry or {}).get("track", {}) if isinstance(entry, dict) else {}
        points = (points or {}).get("points") if isinstance(points, dict) else None
        if not points:
            return None

        # Кроп той же пропорции, что и выход: тогда после масштабирования
        # ничего не искажается и не остаётся полей.
        crop_h = src_h
        crop_w = max(2, int(crop_h * short.width / short.height)) & ~1
        expression = crop_expression([(float(t), float(x)) for t, x in points], crop_w, src_w)
        return {"x": expression, "w": crop_w, "h": crop_h}

    def _edl(self, ctx: StageContext) -> Edl:
        """Ось времени. Нет артефакта — правок нет, а не ошибка.

        Стадия timeline необязательна: без неё рендер работает ровно как
        раньше, и это единственная развилка на весь модуль.
        """
        artifact = Artifact(ctx.paths.analysis / "timeline.json")
        if not artifact.exists():
            return Edl.identity()
        try:
            return Edl.from_dict(artifact.read_json().get("edl", {}))
        except (ValueError, KeyError, TypeError):
            ctx.log.warning("ось времени не читается — рендер без правок")
            return Edl.identity()

    @staticmethod
    def _split_for(clip, cams, framing, src_w, src_h, short):
        """Раскладка сплита для клипа — если она вообще применима.

        Полноэкранная камера сплита не требует: делить кадр, где и так одно
        лицо, значит показать его дважды.
        """
        if framing.layout != "split":
            return None
        cam = RenderStage._cam_entry(clip, cams)
        if cam is None:
            return None
        return plan_split(
            src_w, src_h, short.width, short.height,
            (cam["x"], cam["y"], cam["width"], cam["height"]),
            face=RenderStage._rect(cam, "face"),
            content=RenderStage._rect(cam, "content"),
            anchor=framing.anchor,
            top_share=framing.split_top_share,
            face_zoom=framing.face_zoom,
            face_vertical=framing.face_vertical,
        )

    @staticmethod
    def _background_kind(framing: Framing, plan, has_video: bool) -> str:
        if not has_video:
            return "solid"
        if plan.full_bleed:
            return "none"
        if framing.background == "color":
            return "solid"
        return "blur" if framing.blur_sigma > 0 else "sharp"

    def _command(
        self,
        *,
        source,
        start: float,
        duration: float,
        subtitle_name: str | None,
        loudnorm: bool = True,
        output=None,
        has_video: bool,
        split=None,
        framing: Framing,
        plan,
        width: int,
        height: int,
        crf: int,
        fps: int | None = None,
        cut_video: str = "",
        cut_audio: str = "",
        split_x=None,
        track=None,
        pip=None,
        camera=None,
        camera_x=None,
        encoder: str = "cpu",
        pix_fmt: str,
        faststart: bool,
        lufs: float,
    ) -> list[str]:
        """Собирает вызов ffmpeg.

        `-ss` стоит до `-i` — это быстрая перемотка по контейнеру; точность
        обеспечивается перекодированием.
        """
        # Аппаратный путь требует объявить устройство до входного файла,
        # загрузить кадры в память видеокарты последним фильтром и только
        # потом кодировать — отсюда три части вместо одной.
        device_args, codec_args, hw_tail = video_args(encoder, crf=crf, pix_fmt=pix_fmt)
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", *device_args]

        # Пути абсолютные: ffmpeg запускается из каталога субтитров, потому что
        # экранирование пути внутри строки фильтра libass слишком хрупкое.
        source = source.resolve()
        output = output.resolve()

        if has_video:
            args += ["-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
            video_filter = build_layout_filter(
                framing, plan, width, height,
                split=split, split_x=split_x,
                track=track,
                pip=pip,
                camera=camera, camera_x=camera_x,
                subtitle_name=subtitle_name,
                fonts_dir=escape_for_filter(fonts_dir()),
                fps=fps,
            )
            if hw_tail:
                # Хвост вставляется перед меткой выхода: [v] должна остаться
                # последней, иначе ffmpeg не найдёт, что кодировать.
                video_filter = video_filter.replace("[v]", f"{hw_tail}[v]")
            if cut_video:
                video_filter = _prepend_cut(video_filter, cut_video)
            args += ["-filter_complex", video_filter, "-map", "[v]", "-map", "0:a:0"]
        else:
            args += [
                "-f", "lavfi",
                "-i", f"color=c={framing.color}:s={width}x{height}:r=30:d={duration:.3f}",
                "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
                "-filter_complex",
                f"[0:v]subtitles={subtitle_name}:fontsdir={escape_for_filter(fonts_dir())}[v]",
                "-map", "[v]", "-map", "1:a:0",
            ]

        # Звуковые фильтры собираются в один -af: второй такой параметр молча
        # затирает первый, и нормализация громкости просто исчезла бы.
        audio_chain = [cut_audio] if cut_audio else []
        if loudnorm:
            audio_chain.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
        if audio_chain:
            args += ["-af", ",".join(audio_chain)]
        args += codec_args + [
            "-c:a", "aac",
            "-b:a", "160k",
            "-shortest",
            # Пишем во временный файл с расширением .partial (§58), поэтому
            # формат контейнера задаём явно — по имени ffmpeg его не угадает.
            "-f", "mp4",
        ]
        if faststart:
            args += ["-movflags", "+faststart"]
        args.append(str(output))
        return args


def _prepend_cut(chain: str, cut: str) -> str:
    """Ставит отбор кадров перед цепочкой кадрирования.

    Первым, до масштабирования и размытия: обрабатывать кадры, которые всё
    равно будут выброшены, незачем.

    Цепочка ветвится — размытый фон и содержимое читают исходник каждый
    по-своему, — а метку выхода фильтра ffmpeg разрешает потребить лишь
    однажды. Поэтому после отбора ставится `split` ровно на столько веток,
    сколько их в цепочке, и каждая получает свою метку.
    """
    branches = chain.count("[0:v]")
    if branches == 0:
        return chain
    labels = [f"[cut{i}]" for i in range(branches)]
    if branches > 1:
        head = f"[0:v]{cut},split={branches}{''.join(labels)};"
    else:
        head = f"[0:v]{cut}{labels[0]};"
    for label in labels:
        chain = chain.replace("[0:v]", label, 1)
    return head + chain


def render_one(ctx: StageContext, index: int) -> dict[str, Any]:
    """Пересобирает один готовый ролик, не трогая остальные.

    Идёт тем же путём, что и полный прогон: та же подготовка, та же команда
    ffmpeg. Разница только в том, что клип один и запись в указателе
    правится точечно.

    **След в кэше стадии стирается.** После пересборки одного ролика
    записанный ключ перестаёт описывать то, что лежит на диске: остальные
    ролики собраны по прежним настройкам. Оставить его значило бы, что
    следующая «пересобрать всё» ничего не сделает и разнобой останется
    навсегда.
    """
    stage = RenderStage()
    prep = stage._prepare(ctx)  # noqa: SLF001 — тот же модуль, свой же класс

    clip = next((item for item in prep["clips"] if item["index"] == index), None)
    if clip is None:
        raise StageSkipped(f"момента {index} нет среди отобранных")

    entry = stage._render_clip(ctx, prep, clip)  # noqa: SLF001
    if entry is None:
        raise StageSkipped(f"момент {index} собирать нечем")

    catalogue = Artifact(ctx.paths.shorts / SHORTS_INDEX)
    data = catalogue.read_json() if catalogue.exists() else {"files": []}
    files = [item for item in data.get("files", []) if item.get("index") != index]
    files.append(entry)
    # Порядок по времени, а не по времени сборки: ролики идут так же,
    # как шли в записи.
    files.sort(key=lambda item: item.get("start", 0))
    data["files"] = files
    catalogue.write_json(data)

    (ctx.paths.stage_state / f"{RenderStage.name}.json").unlink(missing_ok=True)
    ctx.log.info("ролик %d пересобран, %.1f МБ", index, entry["size_bytes"] / 1024**2)
    return entry
