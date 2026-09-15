import { Link } from "react-router-dom";

import { Button } from "../components/ui/Button";
import { ErrorState } from "../components/ui/ErrorState";

export function NotFoundPage() {
  return (
    <ErrorState
      illustration="404"
      title="Страница не найдена"
      description="Возможно, ссылка устарела или адрес введён неверно."
      action={
        <Link to="/">
          <Button variant="primary">На главную</Button>
        </Link>
      }
    />
  );
}
