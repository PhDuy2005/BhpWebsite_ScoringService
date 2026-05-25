# OMR Module API

## 1. Mục đích module

Module `OMR` phục vụ luồng chấm bài giấy:

- tạo bản in đề có `paperCode`
- lưu snapshot câu hỏi đúng với bản in
- nhận dữ liệu scan từ `ScoringService`
- tự tạo `ExamAttempt` dạng `OMR_IMPORT`
- lưu đáp án và chấm điểm mà không cần client học sinh start attempt

---

## 2. Quy ước xác thực

- frontend hoặc service gọi API cần gửi:
  - `Authorization: Bearer <access_token>`
- `access token` được cấp từ `Management Service`
- khi tạo `ExamPaper`, backend đọc user hiện tại từ claim:
  - `user.id`

---

## 3. API tạo exam paper

### Đường dẫn

`POST /api/v1/omr/exam-papers`

### Mô tả luồng

Nhận `examUuid` và `paperCode` -> kiểm tra đề tồn tại -> kiểm tra mã đề chưa bị trùng trong cùng đề -> random câu hỏi từ các group theo `pickQuestionCount` -> tạo snapshot câu hỏi cố định cho bản in -> lưu `ExamPaper` -> trả danh sách câu hỏi theo thứ tự in.

### Input format

```json
{
  "examUuid": "018f4a60-12ab-7a11-a9d1-7c5d5b5b0001",
  "paperCode": "M001"
}
```

### Output format

```json
{
  "statusCode": 200,
  "message": "Create OMR exam paper",
  "data": {
    "paperUuid": "uuid",
    "examUuid": "uuid",
    "paperCode": "M001",
    "generatedAt": "2026-05-25T10:00:00Z",
    "generatedByUserUuid": "uuid",
    "questions": [
      {
        "questionOrder": 1,
        "sectionQuestionNumber": 1,
        "questionUuid": "uuid",
        "questionType": "MCQ",
        "score": 0.25,
        "fromQuestionGroup": true,
        "groupUuid": "uuid",
        "groupName": "Tích phân"
      }
    ]
  }
}
```

### Exception có thể trả về

- `Exam id is required`
- `Paper code must not be blank`
- `Exam not found with id: {examUuid}`
- `Exam paper already exists with code: {paperCode}`
- `Exam paper must contain at least one question`
- `User id is missing from JWT`
- `Failed to serialize exam paper question snapshot`

---

## 4. API import OMR data

### Đường dẫn

`POST /api/v1/omr/imports`

### Mô tả luồng

Nhận dữ liệu OMR từ `ScoringService` -> tìm `ExamPaper` theo `examUuid + paperCode` -> kiểm tra `externalSubmissionId` nếu có để chống import trùng -> map từng `sectionQuestionNumber` trong từng section về `questionUuid` trong snapshot của mã đề -> tạo `ExamAttempt` với `submitSource = OMR_IMPORT` -> lưu đáp án vào `StudentAnswer` -> tạo final answer -> chấm điểm -> lưu log `OmrImport` -> trả kết quả import.

### Input format

```json
{
  "examUuid": "018f4a60-12ab-7a11-a9d1-7c5d5b5b0001",
  "paperCode": "M001",
  "studentUuid": "018f4a61-22cd-7b11-9fd2-7c5d5b5b0002",
  "externalSubmissionId": "scoring-service-omr-0001",
  "scannedAt": "2026-05-25T10:05:00Z",
  "sections": {
    "mcq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "A"
      },
      {
        "sectionQuestionNumber": 2,
        "rawAnswer": "AD"
      }
    ],
    "tfq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "DSBD"
      }
    ],
    "saq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "|23|,|7"
      }
    ]
  }
}
```

### Output format

```json
{
  "statusCode": 200,
  "message": "Import OMR data",
  "data": {
    "omrImportUuid": "uuid",
    "examUuid": "uuid",
    "paperUuid": "uuid",
    "paperCode": "M001",
    "studentUuid": "uuid",
    "attemptUuid": "uuid",
    "externalSubmissionId": "scoring-service-omr-0001",
    "status": "IMPORTED",
    "score": 8.5,
    "importedAt": "2026-05-25T10:06:00Z"
  }
}
```

### Exception có thể trả về

- `Exam id is required`
- `Paper code must not be blank`
- `Student id is required`
- `Sections are required`
- `OMR sections must contain at least one answer`
- `Section question number is required`
- `Exam paper not found with code: {paperCode}`
- `OMR submission already imported: {externalSubmissionId}`
- `Section question number must be unique in OMR section {questionType}: {sectionQuestionNumber}`
- `Section question number does not belong to OMR section {questionType}: {sectionQuestionNumber}`
- `Failed to serialize OMR import payload`
- các lỗi chấm bài kế thừa từ `ExamAttemptService`

---

## 5. Quy ước sections

`sections` chia dữ liệu OMR thành 3 phần đúng với layout phiếu:

- `mcq`: danh sách đáp án trắc nghiệm
- `tfq`: danh sách đáp án đúng/sai
- `saq`: danh sách đáp án ngắn

Mỗi section dùng `sectionQuestionNumber` riêng, bắt đầu từ `1`.

Ví dụ:

- `sections.mcq[0].sectionQuestionNumber = 1` nghĩa là câu MCQ số 1 trên phiếu
- `sections.tfq[0].sectionQuestionNumber = 1` nghĩa là câu TFQ số 1 trên phiếu
- `sections.saq[0].sectionQuestionNumber = 1` nghĩa là câu SAQ số 1 trên phiếu

Backend sẽ map theo cặp:

```text
questionType + sectionQuestionNumber -> questionOrder nội bộ của ExamPaper -> questionUuid
```

`questionOrder` vẫn tồn tại trong `ExamPaper` để backend lưu snapshot theo thứ tự toàn cục, nhưng `ScoringService` không cần gửi `questionOrder` khi import OMR.

Ví dụ:

```json
{
  "sections": {
    "mcq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "AD"
      }
    ],
    "tfq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "DSBD"
      }
    ],
    "saq": [
      {
        "sectionQuestionNumber": 1,
        "rawAnswer": "|23|,|7"
      }
    ]
  }
}
```

---

## 6. Quy ước rawAnswer

### MCQ

- `A`, `B`, `C`, `D`: học sinh tô đúng 1 lựa chọn
- nếu học sinh tô nhiều hơn 1 lựa chọn, gửi nguyên các lựa chọn đã tô
- ví dụ học sinh tô `A` và `D` thì gửi `AD`
- `null` hoặc rỗng: bỏ trống

### TFQ

- luôn gửi 4 ký tự
- `D`: đúng
- `S`: sai
- `B`: bỏ trống

Ví dụ:

```json
{
  "sectionQuestionNumber": 2,
  "rawAnswer": "DSBD"
}
```

### SAQ

Với OMR, `rawAnswer` dùng dấu `|` để ngăn cách 4 cột.

Ví dụ:

- `1|2||` -> normalize thành `12__`
- `|23|,|7` -> normalize thành `_M,7`
- `-|1|,|2` -> normalize thành `-1,2`

Quy ước từng cột:

- cột rỗng -> `_`
- cột có đúng 1 ký tự hợp lệ -> giữ nguyên
- cột có nhiều hơn 1 ký tự -> `M`

Nếu `normalizedAnswer` có `M`, câu `SAQ` thực tế sẽ không khớp đáp án chuẩn và bị tính sai.

---

## 7. Ghi chú nghiệp vụ

- OMR không dùng `POST /api/v1/student/exams/{examUuid}/attempts`
- `attemptUuid` được `ExamService` tự tạo trong lúc import OMR
- `ExamPaper` là snapshot bản in, giúp `sectionQuestionNumber` trong từng section map chính xác về `questionUuid`
- nếu đề có group random, random xảy ra khi tạo `ExamPaper`, không xảy ra khi import OMR
- `OmrImport` lưu lại payload scan và attempt được tạo để phục vụ audit/debug
