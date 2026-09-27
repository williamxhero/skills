import csv
import io
import json
import os
import sys
import tempfile


EXPECTED_HEADER = ["id", "title", "status"]
ALLOWED_STATUSES = {"todo", "in_progress", "done"}


class ConversionError(Exception):
    pass


def _absolute_normalized(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _real_normalized(path):
    return os.path.normcase(os.path.realpath(path))


def _paths_collide(input_path, output_path):
    if _absolute_normalized(input_path) == _absolute_normalized(output_path):
        return True
    if _real_normalized(input_path) == _real_normalized(output_path):
        return True

    try:
        return os.path.exists(input_path) and os.path.exists(output_path) and os.path.samefile(
            input_path, output_path
        )
    except OSError:
        return False


def _validate_quote_grammar(content):
    in_quotes = False
    field_start = True
    after_closing_quote = False
    position = 0

    while position < len(content):
        character = content[position]
        if in_quotes:
            if character == '"':
                if position + 1 < len(content) and content[position + 1] == '"':
                    position += 2
                    continue
                in_quotes = False
                after_closing_quote = True
            elif character == "\r":
                if position + 1 >= len(content) or content[position + 1] != "\n":
                    raise ConversionError("input CSV contains an invalid line ending")
            position += 1
            continue

        if after_closing_quote:
            if character == ",":
                field_start = True
                after_closing_quote = False
            elif character == "\r":
                if position + 1 >= len(content) or content[position + 1] != "\n":
                    raise ConversionError("input CSV contains an invalid line ending")
                field_start = True
                after_closing_quote = False
            elif character == "\n":
                field_start = True
                after_closing_quote = False
            else:
                raise ConversionError("input CSV contains invalid characters after a quote")
        elif character == '"':
            if not field_start:
                raise ConversionError("input CSV contains a quote in an unquoted field")
            in_quotes = True
            field_start = False
        elif character == ",":
            field_start = True
        elif character == "\r":
            if position + 1 >= len(content) or content[position + 1] != "\n":
                raise ConversionError("input CSV contains an invalid line ending")
            field_start = True
            position += 1
        elif character == "\n":
            field_start = True
        else:
            field_start = False
        position += 1

    if in_quotes:
        raise ConversionError("input CSV contains an unterminated quoted field")


def _parse_tasks(input_path):
    try:
        with open(input_path, "r", encoding="utf-8-sig", newline="") as input_file:
            content = input_file.read()
            _validate_quote_grammar(content)
            reader = csv.reader(io.StringIO(content), strict=True)
            try:
                header = next(reader)
            except StopIteration as exc:
                raise ConversionError("input CSV is missing the required header") from exc

            if header != EXPECTED_HEADER:
                raise ConversionError("input CSV header must be exactly id,title,status")

            tasks = []
            seen_ids = set()
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(EXPECTED_HEADER):
                    raise ConversionError(
                        f"row {row_number} must contain exactly three fields"
                    )

                task = {
                    field_name: field_value.strip()
                    for field_name, field_value in zip(EXPECTED_HEADER, row)
                }
                if any(not task[field_name] for field_name in EXPECTED_HEADER):
                    raise ConversionError(f"row {row_number} contains an empty required field")
                if task["status"] not in ALLOWED_STATUSES:
                    raise ConversionError(
                        f"row {row_number} has an unsupported status: {task['status']}"
                    )
                if task["id"] in seen_ids:
                    raise ConversionError(f"row {row_number} duplicates task id: {task['id']}")

                seen_ids.add(task["id"])
                tasks.append(task)
            return tasks
    except ConversionError:
        raise
    except (csv.Error, OSError, UnicodeError) as exc:
        raise ConversionError(f"could not read input CSV: {exc}") from exc


def _serialize_tasks(tasks):
    try:
        return (
            json.dumps(
                {"tasks": tasks},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise ConversionError(f"could not serialize output JSON: {exc}") from exc


def _deliver(output_path, payload):
    output_path = os.path.abspath(os.path.normpath(output_path))
    output_directory = os.path.dirname(output_path) or os.curdir

    if os.path.islink(output_path):
        raise ConversionError("output path must not be a symbolic link")
    if not os.path.isdir(output_directory):
        raise ConversionError("output directory does not exist")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output_directory,
            prefix=f".{os.path.basename(output_path)}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if os.path.islink(output_path):
            raise ConversionError("output path must not be a symbolic link")
        os.replace(temporary_path, output_path)
        temporary_path = None
    except ConversionError:
        raise
    except OSError as exc:
        raise ConversionError(f"could not write output JSON: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def convert(input_path, output_path):
    if _paths_collide(input_path, output_path):
        raise ConversionError("input and output paths must refer to different files")

    tasks = _parse_tasks(input_path)
    payload = _serialize_tasks(tasks)
    _deliver(output_path, payload)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else list(argv)
    if len(arguments) != 2:
        print(
            "error: expected exactly two positional arguments: INPUT_CSV OUTPUT_JSON",
            file=sys.stderr,
        )
        return 1

    try:
        convert(arguments[0], arguments[1])
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
