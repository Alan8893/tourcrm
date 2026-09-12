# TourCRM — File Processing Pipeline

## 1. Назначение

Документ определяет безопасную обработку загружаемых файлов: документы, фотографии, GPX, медиа и экспортные артефакты.

## 2. Processing lifecycle

Рекомендуемый lifecycle:

`uploaded → quarantined → scanning → validated → available`

Ошибочные объекты переходят в:

`rejected` или `processing_failed`.

До успешной проверки файл не должен быть доступен как рабочий объект.

## 3. File metadata

БД должна хранить metadata, а не обязательно содержимое файла:

- id;
- storage key;
- original filename;
- normalized filename;
- MIME type claimed;
- MIME type detected;
- size;
- checksum;
- uploader;
- owner/reference;
- processing status;
- created_at;
- validated_at;
- rejection reason.

## 4. MIME and size policy

Для каждого file category должен существовать allowlist MIME types и максимальный размер.

Значения должны быть конфигурируемыми и документированы по типу объекта. Нельзя доверять только MIME type, присланному клиентом.

## 5. Security pipeline

Минимально:

1. upload authentication/authorization;
2. size check;
3. content type detection;
4. checksum calculation;
5. antivirus/malware scan where operationally possible;
6. format validation;
7. metadata extraction;
8. optional preview/thumbnail generation;
9. publish to available state.

## 6. Images

Для фотографий при необходимости создаются derived variants. Исходный файл сохраняется отдельно. EXIF/GPS metadata должна обрабатываться по privacy policy.

## 7. GPX

GPX processing должен:

- валидировать XML/GPX format;
- ограничивать resource consumption;
- извлекать допустимые track/route points;
- считать checksum;
- вычислять derived statistics только по валидным данным;
- сохранять исходный файл отдельно.

Malformed GPX не должен приводить к аварии worker/API.

## 8. Documents

Document content не должен становиться публичным только из-за наличия object storage key. Download должен выполняться через authorized backend/presigned mechanism с ограниченным сроком действия.

## 9. Deduplication

Checksum может использоваться для обнаружения полного совпадения файлов, но дедупликация не должна нарушать ownership/access semantics.

## 10. Quarantine

Rejected/suspicious files изолируются и недоступны обычным пользователям. Должен быть предусмотрен operational review/removal flow.

## 11. Processing failures

Ошибки background processing должны быть retryable, если ошибка временная. Неисправимый файл должен перейти в deterministic failed state с безопасной диагностикой.

## 12. Acceptance criteria

- [ ] есть explicit file lifecycle;
- [ ] upload не публикуется до validation;
- [ ] MIME/size allowlists определены;
- [ ] checksum вычисляется;
- [ ] security scanning предусмотрен;
- [ ] GPX validation безопасен;
- [ ] object storage access authorization enforced;
- [ ] suspicious files изолируются;
- [ ] processing failures observable;
- [ ] PII/EXIF handling соответствует privacy policy.
